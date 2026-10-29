import uuid
from datetime import UTC, datetime, timedelta

import pytest

from flagman.core.errors import AppError
from flagman.pricing.domain import IntervalKind, TariffPrices, calculate
from flagman.rentals.domain import (
    CarLockFailedError,
    CarStateStaleError,
    ChargeRetryRules,
    DoorsOpenError,
    FinishCheck,
    FinishRules,
    IgnitionOnError,
    InspectionRequiredError,
    OutsideEndZoneError,
    ParkCheck,
    PlannedPayment,
    Rental,
    RentalBusyError,
    RentalStatus,
    RentalTransition,
    RentalTransitionNotAllowedError,
    SettlementStep,
    TripInterval,
    TripWarning,
    TripWarningRules,
    debt_key,
    ensure_finish_allowed,
    ensure_toggle_allowed,
    hold_key,
    lock_refusal,
    next_charge_attempt,
    payment_key,
    plan_settlement,
    rental_intervals,
    trip_warnings,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
PRICES = TariffPrices(
    driving_per_minute=50,
    parked_per_minute=10,
    waiting_per_minute=20,
    free_waiting_minutes=15,
    hold_amount=3000,
)
RULES = FinishRules(max_state_age=timedelta(seconds=60))
FRESH = FinishCheck(recorded_at=NOW - timedelta(seconds=10), ignition_on=False, in_end_zone=True)


def _rental(
    status: RentalStatus = RentalStatus.IN_TRIP,
    *,
    reserved: timedelta = timedelta(minutes=40),
    waited: timedelta | None = timedelta(minutes=20),
    ended_at: datetime | None = None,
    command_until: datetime | None = None,
) -> Rental:
    reserved_at = NOW - reserved
    return Rental(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        car_id=uuid.uuid4(),
        status=status,
        tariff_version_id=uuid.uuid4(),
        reserved_at=reserved_at,
        free_wait_until=reserved_at + 15 * MINUTE,
        expires_at=reserved_at + 45 * MINUTE,
        trip_started_at=reserved_at + waited if waited is not None else None,
        ended_at=ended_at,
        cancel_reason=None,
        command_until=command_until,
    )


def test_the_wait_runs_until_the_trip_starts_and_the_open_interval_until_now() -> None:
    rental = _rental()
    start = rental.trip_started_at
    assert start is not None
    stored = (
        TripInterval(IntervalKind.DRIVING, start, start + 10 * MINUTE),
        TripInterval(IntervalKind.PARKED, start + 10 * MINUTE, None),
    )

    [waiting, driving, parked] = rental_intervals(rental, stored, NOW)

    assert (waiting.kind, waiting.start, waiting.end) == (
        IntervalKind.WAITING,
        rental.reserved_at,
        start,
    )
    assert driving.length == 10 * MINUTE
    assert (parked.kind, parked.end) == (IntervalKind.PARKED, NOW)


def test_an_ended_rental_is_billed_until_its_end() -> None:
    ended_at = NOW - 5 * MINUTE
    rental = _rental(RentalStatus.FINISHED, ended_at=ended_at)
    start = rental.trip_started_at
    assert start is not None

    *_, driving = rental_intervals(rental, (TripInterval(IntervalKind.DRIVING, start, None),), NOW)

    assert driving.end == ended_at


def test_a_reservation_without_a_trip_is_all_waiting() -> None:
    rental = _rental(RentalStatus.RESERVED, reserved=timedelta(minutes=25), waited=None)

    [waiting] = rental_intervals(rental, (), NOW)

    assert waiting.length == 25 * MINUTE


def test_the_cost_counts_the_paid_wait_the_driving_and_the_parking() -> None:
    rental = _rental()
    start = rental.trip_started_at
    assert start is not None
    stored = (
        TripInterval(IntervalKind.DRIVING, start, start + 7 * MINUTE),
        TripInterval(IntervalKind.PARKED, start + 7 * MINUTE, start + 10 * MINUTE),
        TripInterval(IntervalKind.DRIVING, start + 10 * MINUTE, None),
    )

    quote = calculate(rental_intervals(rental, stored, NOW), PRICES)

    lines = {line.kind: (line.minutes, line.free_minutes, line.amount) for line in quote.lines}
    # 20 minutes waited, 15 of them free; 7 + 10 minutes driving; 3 parked.
    assert lines == {
        IntervalKind.WAITING: (5, 15, 100),
        IntervalKind.DRIVING: (17, 0, 850),
        IntervalKind.PARKED: (3, 0, 30),
    }
    assert quote.total == 980


@pytest.mark.parametrize(
    ("rental", "submitted", "check", "error"),
    [
        (_rental(RentalStatus.RESERVED), True, FRESH, RentalTransitionNotAllowedError),
        (_rental(RentalStatus.PAID), True, FRESH, RentalTransitionNotAllowedError),
        (_rental(command_until=NOW + MINUTE), True, FRESH, RentalBusyError),
        (_rental(), False, FRESH, InspectionRequiredError),
        (
            _rental(),
            True,
            FinishCheck(recorded_at=None, ignition_on=False, in_end_zone=True),
            CarStateStaleError,
        ),
        (
            _rental(),
            True,
            FinishCheck(NOW - timedelta(seconds=61), ignition_on=False, in_end_zone=True),
            CarStateStaleError,
        ),
        (
            _rental(),
            True,
            FinishCheck(NOW, ignition_on=False, in_end_zone=False),
            OutsideEndZoneError,
        ),
        (_rental(), True, FinishCheck(NOW, ignition_on=True, in_end_zone=True), IgnitionOnError),
    ],
)
def test_the_finish_is_checked_one_reason_at_a_time(
    rental: Rental, submitted: bool, check: FinishCheck, error: type[Exception]
) -> None:
    with pytest.raises(error):
        ensure_finish_allowed(rental, submitted, check, RULES, NOW)


@pytest.mark.parametrize("status", [RentalStatus.IN_TRIP, RentalStatus.PARKED])
def test_a_trip_passing_every_check_may_finish(status: RentalStatus) -> None:
    ensure_finish_allowed(_rental(status), True, FRESH, RULES, NOW)
    ensure_finish_allowed(
        _rental(status), True, FinishCheck(NOW - timedelta(seconds=60), False, True), RULES, NOW
    )


STANDING = ParkCheck(recorded_at=NOW - timedelta(seconds=10), ignition_on=False)


@pytest.mark.parametrize(
    ("rental", "check", "error"),
    [
        (_rental(command_until=NOW + MINUTE), STANDING, RentalBusyError),
        (_rental(), ParkCheck(recorded_at=None, ignition_on=False), CarStateStaleError),
        (_rental(), ParkCheck(NOW - timedelta(seconds=61), ignition_on=False), CarStateStaleError),
        (_rental(), ParkCheck(NOW, ignition_on=True), IgnitionOnError),
    ],
)
def test_parking_needs_a_standing_car(
    rental: Rental, check: ParkCheck, error: type[Exception]
) -> None:
    with pytest.raises(error):
        ensure_toggle_allowed(rental, RentalTransition.PARK, check, RULES, NOW)


def test_a_standing_car_may_park() -> None:
    ensure_toggle_allowed(_rental(), RentalTransition.PARK, STANDING, RULES, NOW)


def test_driving_on_does_not_look_at_the_car_but_waits_for_a_command() -> None:
    moving = ParkCheck(recorded_at=None, ignition_on=True)
    ensure_toggle_allowed(_rental(RentalStatus.PARKED), RentalTransition.RESUME, moving, RULES, NOW)
    busy = _rental(RentalStatus.PARKED, command_until=NOW + MINUTE)
    with pytest.raises(RentalBusyError):
        ensure_toggle_allowed(busy, RentalTransition.RESUME, moving, RULES, NOW)


@pytest.mark.parametrize(
    ("reason", "error"),
    [
        ("doors_open", DoorsOpenError),
        ("ignition_on", IgnitionOnError),
        ("engine_fault", CarLockFailedError),
    ],
)
def test_a_lock_refusal_is_told_in_the_clients_terms(reason: str, error: type[AppError]) -> None:
    assert isinstance(lock_refusal(reason), error)


@pytest.mark.parametrize(
    ("total", "hold", "planned"),
    [
        (1200, 3000, (PlannedPayment(SettlementStep.CAPTURE, 1200),)),
        (3000, 3000, (PlannedPayment(SettlementStep.CAPTURE, 3000),)),
        (
            4500,
            3000,
            (
                PlannedPayment(SettlementStep.CAPTURE, 3000),
                PlannedPayment(SettlementStep.EXTRA, 1500),
            ),
        ),
        (0, 3000, (PlannedPayment(SettlementStep.VOID, None),)),
        (700, None, (PlannedPayment(SettlementStep.CHARGE, 700),)),
        (0, None, ()),
    ],
)
def test_a_total_is_taken_from_the_hold_first(
    total: int, hold: int | None, planned: tuple[PlannedPayment, ...]
) -> None:
    assert plan_settlement(total, hold) == planned


def test_a_negative_total_is_a_bug() -> None:
    with pytest.raises(ValueError, match="negative"):
        plan_settlement(-1, 3000)


def test_every_payment_of_a_rental_has_a_key_of_its_own() -> None:
    rental_id = uuid.uuid4()
    keys = {
        hold_key(rental_id, 1),
        hold_key(rental_id, 2),
        *(payment_key(rental_id, step) for step in SettlementStep),
        debt_key(rental_id, 1),
        debt_key(rental_id, 2),
    }

    assert len(keys) == 8
    assert all(str(rental_id) in key and len(key) <= 128 for key in keys)


def test_a_declined_debt_is_retried_a_limited_number_of_times() -> None:
    rules = ChargeRetryRules(max_attempts=3, interval=timedelta(hours=1))

    assert next_charge_attempt(0, rules, NOW) == NOW + timedelta(hours=1)
    assert next_charge_attempt(2, rules, NOW) == NOW + timedelta(hours=1)
    assert next_charge_attempt(3, rules, NOW) is None


@pytest.mark.parametrize(
    ("battery", "range_km", "warnings"),
    [
        (80, 200, ()),
        (14, 200, (TripWarning.LOW_BATTERY,)),
        (15, 29, (TripWarning.LOW_RANGE,)),
        (10, 20, (TripWarning.LOW_BATTERY, TripWarning.LOW_RANGE)),
        (None, None, ()),
    ],
)
def test_the_trip_warns_about_the_charge_and_the_range(
    battery: int | None, range_km: int | None, warnings: tuple[TripWarning, ...]
) -> None:
    rules = TripWarningRules(low_battery_pct=15, low_range_km=30)

    assert trip_warnings(battery, range_km, rules) == warnings
