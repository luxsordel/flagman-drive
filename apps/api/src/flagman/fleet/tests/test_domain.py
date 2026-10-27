import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import (
    RENTAL_TRANSITIONS,
    AvailabilityRules,
    Car,
    CarModelSpec,
    CarState,
    CarStatus,
    CarTransition,
    CarTransitionNotAllowedError,
    MapArea,
    SnapshotFromFutureError,
    StaffCarFilter,
    apply_transition,
    car_on_map,
    check_snapshot_time,
    emulator_car_id,
    is_low_battery,
    is_rentable,
    matches_staff_filter,
    normalize_plate,
    normalize_vin,
    range_left_km,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
RULES = AvailabilityRules(min_battery_pct=20, max_state_age=timedelta(minutes=5))
# The default signature window of telematics requests (TELEMATICS_SIGNATURE_TOLERANCE_SECONDS).
MAX_CLOCK_SKEW = timedelta(seconds=300)
MINSK = GeoPoint(lat=53.9006, lon=27.5590)

ALLOWED = {
    (CarStatus.AVAILABLE, CarTransition.RESERVE): CarStatus.RESERVED,
    (CarStatus.RESERVED, CarTransition.CANCEL_RESERVATION): CarStatus.AVAILABLE,
    (CarStatus.RESERVED, CarTransition.START_RENTAL): CarStatus.IN_RENTAL,
    (CarStatus.IN_RENTAL, CarTransition.END_RENTAL): CarStatus.AVAILABLE,
    (CarStatus.AVAILABLE, CarTransition.SEND_TO_SERVICE): CarStatus.SERVICE,
    (CarStatus.OFFLINE, CarTransition.SEND_TO_SERVICE): CarStatus.SERVICE,
    (CarStatus.SERVICE, CarTransition.RETURN_FROM_SERVICE): CarStatus.AVAILABLE,
    (CarStatus.AVAILABLE, CarTransition.GO_OFFLINE): CarStatus.OFFLINE,
    (CarStatus.SERVICE, CarTransition.GO_OFFLINE): CarStatus.OFFLINE,
    (CarStatus.OFFLINE, CarTransition.COME_ONLINE): CarStatus.AVAILABLE,
}


def _state(
    battery_pct: int = 80, age: timedelta = timedelta(seconds=10), *, ignition_on: bool = False
) -> CarState:
    return CarState(
        location=MINSK,
        battery_pct=battery_pct,
        doors_locked=True,
        ignition_on=ignition_on,
        engine_locked=False,
        recorded_at=NOW - age,
    )


@pytest.mark.parametrize(("source", "transition"), list(ALLOWED))
def test_allowed_transitions(source: CarStatus, transition: CarTransition) -> None:
    assert apply_transition(source, transition) is ALLOWED[source, transition]


@pytest.mark.parametrize(
    ("source", "transition"),
    [
        (source, transition)
        for source in CarStatus
        for transition in CarTransition
        if (source, transition) not in ALLOWED
    ],
)
def test_other_transitions_are_refused(source: CarStatus, transition: CarTransition) -> None:
    with pytest.raises(CarTransitionNotAllowedError):
        apply_transition(source, transition)


@pytest.mark.parametrize("status", [CarStatus.RESERVED, CarStatus.IN_RENTAL])
def test_a_car_in_a_rental_is_not_taken_out_by_the_fleet(status: CarStatus) -> None:
    for transition in set(CarTransition) - RENTAL_TRANSITIONS:
        with pytest.raises(CarTransitionNotAllowedError):
            apply_transition(status, transition)


def test_an_available_car_with_fresh_telemetry_and_charge_is_rentable() -> None:
    assert is_rentable(CarStatus.AVAILABLE, _state(), RULES, NOW)


@pytest.mark.parametrize("status", set(CarStatus) - {CarStatus.AVAILABLE})
def test_only_available_cars_are_rentable(status: CarStatus) -> None:
    assert not is_rentable(status, _state(), RULES, NOW)


def test_a_car_without_telemetry_is_not_rentable() -> None:
    assert not is_rentable(CarStatus.AVAILABLE, None, RULES, NOW)


def test_a_car_with_its_ignition_on_is_not_rentable() -> None:
    assert not is_rentable(CarStatus.AVAILABLE, _state(ignition_on=True), RULES, NOW)


@pytest.mark.parametrize(("battery_pct", "rentable"), [(19, False), (20, True), (100, True)])
def test_battery_threshold(battery_pct: int, rentable: bool) -> None:
    assert is_rentable(CarStatus.AVAILABLE, _state(battery_pct=battery_pct), RULES, NOW) is rentable


@pytest.mark.parametrize(
    ("age", "rentable"),
    [
        (RULES.max_state_age, True),
        (RULES.max_state_age + timedelta(seconds=1), False),
        # Slightly ahead telematics clocks are within the skew window and still count as fresh.
        (-timedelta(seconds=30), True),
    ],
)
def test_telemetry_age_threshold(age: timedelta, rentable: bool) -> None:
    assert is_rentable(CarStatus.AVAILABLE, _state(age=age), RULES, NOW) is rentable


def test_snapshot_ahead_stays_fresh_at_most_for_the_skew_longer() -> None:
    # The accepted cost of trusting the car's clock within the window (docs/02-architecture.md).
    ahead = _state(age=-MAX_CLOCK_SKEW)
    limit = NOW + MAX_CLOCK_SKEW + RULES.max_state_age

    assert is_rentable(CarStatus.AVAILABLE, ahead, RULES, limit)
    assert not is_rentable(CarStatus.AVAILABLE, ahead, RULES, limit + timedelta(seconds=1))


def test_snapshot_within_the_clock_skew_is_accepted() -> None:
    check_snapshot_time(NOW + MAX_CLOCK_SKEW, NOW, MAX_CLOCK_SKEW)


def test_snapshot_from_the_future_is_refused() -> None:
    with pytest.raises(SnapshotFromFutureError):
        check_snapshot_time(NOW + MAX_CLOCK_SKEW + timedelta(seconds=1), NOW, MAX_CLOCK_SKEW)


@pytest.mark.parametrize("battery_pct", [-1, 101])
def test_battery_out_of_range_is_refused(battery_pct: int) -> None:
    with pytest.raises(ValueError, match="battery_pct"):
        _state(battery_pct=battery_pct)


def test_naive_snapshot_time_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        CarState(
            location=MINSK,
            battery_pct=50,
            doors_locked=True,
            ignition_on=False,
            engine_locked=False,
            recorded_at=datetime(2026, 10, 24, 12, 0),  # noqa: DTZ001 - the case under test
        )


@pytest.mark.parametrize(("lat", "lon"), [(90.1, 0), (-90.1, 0), (0, 180.1), (0, -180.1)])
def test_coordinates_out_of_range_are_refused(lat: float, lon: float) -> None:
    with pytest.raises(ValueError, match="coordinates"):
        GeoPoint(lat=lat, lon=lon)


@pytest.mark.parametrize(
    ("raw", "plate"), [("1234 AB-7", "1234 AB-7"), (" 0001 ex-5 ", "0001 EX-5")]
)
def test_plate_is_normalized(raw: str, plate: str) -> None:
    assert normalize_plate(raw) == plate


@pytest.mark.parametrize(
    "raw",
    [
        "1234AB-7",
        "1234 AB 7",
        "123 AB-7",
        "1234 AD-7",  # D has no Cyrillic twin
        "1234 AB-9",
        "1234 АВ-7",  # noqa: RUF001 - Cyrillic A and VE are the case under test
        "١٢٣٤ AB-7",  # Arabic-Indic digits
    ],
)
def test_invalid_plates_are_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="plate"):
        normalize_plate(raw)


def test_vin_is_normalized() -> None:
    assert normalize_vin(" lgxce4cb0r0000001 ") == "LGXCE4CB0R0000001"


@pytest.mark.parametrize("raw", ["LGXCE4CB0R000000", "LGXCE4CB0R00000011", "LGXCE4CBOR0000001"])
def test_invalid_vins_are_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="VIN"):
        normalize_vin(raw)


def test_emulator_car_ids_match_the_emulator() -> None:
    # Reference vector shared with services/car-emulator (tests/test_domain.py).
    assert emulator_car_id(42, 0) == uuid.UUID("a0b2fa60-ac1d-5ee4-b7a5-2b037e18bd35")
    assert emulator_car_id(42, 29) == uuid.UUID("c6069517-01a5-59b6-a91c-291f0738f6d3")


@pytest.mark.parametrize(
    ("battery_pct", "range_km"), [(100, 400), (50, 200), (33, 132), (1, 4), (0, 0)]
)
def test_range_left_follows_the_charge(battery_pct: int, range_km: int) -> None:
    assert range_left_km(400, battery_pct) == range_km


def test_range_left_is_rounded_down() -> None:
    assert range_left_km(333, 50) == 166


def _car(state: CarState | None) -> Car:
    return Car(
        id=uuid.uuid4(),
        model_id=uuid.uuid4(),
        model=CarModelSpec("Geely", "Geometry C", 5, 70_000, 400, "https://cdn.test/c.png"),
        plate="1234 AB-7",
        vin="LB37622Z9NX000001",
        telematics_id=uuid.uuid4(),
        status=CarStatus.AVAILABLE,
        state=state,
    )


def test_car_on_map_shows_only_what_the_client_needs() -> None:
    car = _car(_state(battery_pct=50))

    shown = car_on_map(car, 1234.6)

    assert (shown.id, shown.brand, shown.model) == (car.id, "Geely", "Geometry C")
    assert shown.photo_url == "https://cdn.test/c.png"
    assert shown.location == MINSK
    assert (shown.battery_pct, shown.range_km, shown.distance_m) == (50, 200, 1235)
    assert not {"vin", "plate", "telematics_id", "doors_locked", "ignition_on"} & set(vars(shown))


def test_car_on_map_without_the_clients_point_has_no_distance() -> None:
    assert car_on_map(_car(_state()), None).distance_m is None


def test_a_car_without_telemetry_is_never_on_the_map() -> None:
    with pytest.raises(ValueError, match="without telemetry"):
        car_on_map(_car(None), None)


def test_map_area_needs_min_below_max() -> None:
    MapArea(min_lat=53.8, min_lon=27.4, max_lat=54.0, max_lon=27.7)

    with pytest.raises(ValueError, match="min below max"):
        MapArea(min_lat=54.0, min_lon=27.4, max_lat=53.8, max_lon=27.7)
    with pytest.raises(ValueError, match="min below max"):
        MapArea(min_lat=53.8, min_lon=27.7, max_lat=54.0, max_lon=27.7)


def test_map_area_corners_are_real_coordinates() -> None:
    with pytest.raises(ValueError, match="out of range"):
        MapArea(min_lat=-91, min_lon=27.4, max_lat=54.0, max_lon=27.7)


@pytest.mark.parametrize(("battery_pct", "low"), [(19, True), (20, False), (0, True), (100, False)])
def test_low_battery_is_below_the_rentable_charge(battery_pct: int, low: bool) -> None:
    assert is_low_battery(_state(battery_pct=battery_pct), RULES) is low


def test_a_car_without_telemetry_is_not_low_on_battery() -> None:
    assert not is_low_battery(None, RULES)


def _with(status: CarStatus, battery_pct: int) -> Car:
    return replace(_car(_state(battery_pct=battery_pct)), status=status)


def test_an_empty_staff_filter_lets_every_car_through() -> None:
    assert matches_staff_filter(_with(CarStatus.IN_RENTAL, 5), StaffCarFilter(), RULES)
    assert matches_staff_filter(_car(None), StaffCarFilter(), RULES)


@pytest.mark.parametrize(
    ("car_filter", "status", "battery_pct", "matches"),
    [
        (StaffCarFilter(statuses=frozenset({CarStatus.SERVICE})), CarStatus.SERVICE, 80, True),
        (StaffCarFilter(statuses=frozenset({CarStatus.SERVICE})), CarStatus.AVAILABLE, 80, False),
        (StaffCarFilter(low_battery=True), CarStatus.AVAILABLE, 10, True),
        (StaffCarFilter(low_battery=True), CarStatus.AVAILABLE, 80, False),
        (
            StaffCarFilter(statuses=frozenset({CarStatus.OFFLINE}), low_battery=True),
            CarStatus.OFFLINE,
            80,
            False,
        ),
    ],
    ids=["status-in", "status-out", "low", "not-low", "both-must-match"],
)
def test_staff_filter(
    car_filter: StaffCarFilter, status: CarStatus, battery_pct: int, matches: bool
) -> None:
    assert matches_staff_filter(_with(status, battery_pct), car_filter, RULES) is matches
