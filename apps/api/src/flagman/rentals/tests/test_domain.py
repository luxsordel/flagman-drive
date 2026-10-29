import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from flagman.rentals.domain import (
    ACTIVE_STATUSES,
    MAX_DAMAGES,
    MAX_INSPECTION_PHOTOS,
    MIN_INSPECTION_PHOTOS,
    CarLocationUnknownError,
    CommandRules,
    Damage,
    DamageSide,
    Inspection,
    InspectionClosedError,
    InspectionDamageLimitError,
    InspectionIncompleteError,
    InspectionPhoto,
    InspectionPhotoLimitError,
    InspectionRequiredError,
    InspectionStage,
    Rental,
    RentalBusyError,
    RentalStatus,
    RentalTransition,
    RentalTransitionNotAllowedError,
    ReservationTerms,
    TooFarFromCarError,
    apply_transition,
    ensure_command_allowed,
    ensure_near_car,
    normalize_damage_comment,
    reservation_deadlines,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

S = RentalStatus
T = RentalTransition

ALLOWED = {
    (S.RESERVED, T.CANCEL): S.CANCELLED,
    (S.RESERVED, T.EXPIRE): S.CANCELLED,
    (S.RESERVED, T.START_TRIP): S.IN_TRIP,
    (S.IN_TRIP, T.PARK): S.PARKED,
    (S.PARKED, T.RESUME): S.IN_TRIP,
    (S.IN_TRIP, T.FINISH): S.FINISHED,
    (S.PARKED, T.FINISH): S.FINISHED,
    (S.IN_TRIP, T.FORCE_FINISH): S.FINISHED,
    (S.PARKED, T.FORCE_FINISH): S.FINISHED,
    (S.FINISHED, T.MARK_PAID): S.PAID,
    (S.FINISHED, T.MARK_PAYMENT_FAILED): S.PAYMENT_FAILED,
    (S.PAYMENT_FAILED, T.MARK_PAID): S.PAID,
}


@pytest.mark.parametrize(("source", "transition"), list(ALLOWED))
def test_allowed_transitions(source: RentalStatus, transition: RentalTransition) -> None:
    assert apply_transition(source, transition) is ALLOWED[(source, transition)]


@pytest.mark.parametrize(
    ("source", "transition"),
    [
        (source, transition)
        for source in RentalStatus
        for transition in RentalTransition
        if (source, transition) not in ALLOWED
    ],
)
def test_every_other_transition_is_refused(
    source: RentalStatus, transition: RentalTransition
) -> None:
    with pytest.raises(RentalTransitionNotAllowedError):
        apply_transition(source, transition)


def test_a_trip_and_a_reservation_are_active_and_the_rest_is_not() -> None:
    assert frozenset({S.RESERVED, S.IN_TRIP, S.PARKED}) == ACTIVE_STATUSES


def test_deadlines_count_from_the_reservation() -> None:
    terms = ReservationTerms(free_wait=timedelta(minutes=15), max_wait=timedelta(minutes=45))

    assert reservation_deadlines(NOW, terms) == (
        NOW + timedelta(minutes=15),
        NOW + timedelta(minutes=45),
    )


@pytest.mark.parametrize(
    ("free", "maximum"),
    [(timedelta(minutes=-1), timedelta(minutes=10)), (timedelta(minutes=5), timedelta(0))],
)
def test_terms_refuse_negative_waits_and_an_empty_reservation(
    free: timedelta, maximum: timedelta
) -> None:
    with pytest.raises(ValueError, match="must"):
        ReservationTerms(free_wait=free, max_wait=maximum)


def test_more_free_minutes_than_the_reservation_make_all_of_it_free() -> None:
    terms = ReservationTerms(free_wait=timedelta(minutes=60), max_wait=timedelta(minutes=45))

    assert reservation_deadlines(NOW, terms) == (
        NOW + timedelta(minutes=45),
        NOW + timedelta(minutes=45),
    )


def test_a_reservation_may_be_free_until_its_end() -> None:
    terms = ReservationTerms(free_wait=timedelta(minutes=15), max_wait=timedelta(minutes=15))

    free_until, expires_at = reservation_deadlines(NOW, terms)

    assert free_until == expires_at


def _rental(**overrides: object) -> Rental:
    rental = Rental(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        car_id=uuid.uuid4(),
        status=RentalStatus.RESERVED,
        tariff_version_id=uuid.uuid4(),
        reserved_at=NOW - timedelta(minutes=10),
        free_wait_until=NOW + timedelta(minutes=5),
        expires_at=NOW + timedelta(minutes=35),
        trip_started_at=None,
        ended_at=None,
        cancel_reason=None,
        command_until=None,
    )
    return replace(rental, **overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "driving"),
    [
        (RentalStatus.RESERVED, False),
        (RentalStatus.IN_TRIP, True),
        (RentalStatus.PARKED, True),
        (RentalStatus.FINISHED, False),
        (RentalStatus.CANCELLED, False),
    ],
)
def test_only_a_trip_or_a_stop_in_it_is_driving(status: RentalStatus, driving: bool) -> None:
    assert _rental(status=status).is_driving is driving


def test_a_reservation_expires_at_its_end_exactly() -> None:
    rental = _rental(expires_at=NOW)

    assert not rental.is_expired(NOW - timedelta(microseconds=1))
    assert rental.is_expired(NOW)


def test_a_command_in_flight_holds_the_reservation_past_its_end() -> None:
    rental = _rental(
        expires_at=NOW - timedelta(seconds=1), command_until=NOW + timedelta(seconds=3)
    )

    assert rental.is_busy(NOW)
    assert not rental.is_expired(NOW)
    assert rental.is_expired(NOW + timedelta(seconds=3))


def test_a_started_trip_never_expires() -> None:
    rental = _rental(status=RentalStatus.IN_TRIP, expires_at=NOW - timedelta(hours=1))

    assert not rental.is_expired(NOW)


def _photos(count: int) -> tuple[InspectionPhoto, ...]:
    return tuple(InspectionPhoto(uuid.uuid4(), NOW) for _ in range(count))


def test_the_inspection_before_is_open_while_the_car_is_reserved_only() -> None:
    inspection = Inspection(stage=InspectionStage.BEFORE)

    inspection.ensure_editable(RentalStatus.RESERVED)
    for status in set(RentalStatus) - {RentalStatus.RESERVED}:
        with pytest.raises(InspectionClosedError):
            inspection.ensure_editable(status)


def test_the_inspection_after_is_open_during_the_trip_only() -> None:
    inspection = Inspection(stage=InspectionStage.AFTER)

    inspection.ensure_editable(RentalStatus.IN_TRIP)
    inspection.ensure_editable(RentalStatus.PARKED)
    with pytest.raises(InspectionClosedError):
        inspection.ensure_editable(RentalStatus.RESERVED)


def test_a_submitted_inspection_takes_nothing_more() -> None:
    inspection = Inspection(
        stage=InspectionStage.BEFORE, photos=_photos(MIN_INSPECTION_PHOTOS), submitted_at=NOW
    )

    with pytest.raises(InspectionClosedError):
        inspection.ensure_photo_fits(RentalStatus.RESERVED)
    with pytest.raises(InspectionClosedError):
        inspection.ensure_damage_fits(RentalStatus.RESERVED)
    with pytest.raises(InspectionClosedError):
        inspection.ensure_submittable(RentalStatus.RESERVED)


def test_photos_are_limited() -> None:
    Inspection(InspectionStage.BEFORE, _photos(MAX_INSPECTION_PHOTOS - 1)).ensure_photo_fits(
        RentalStatus.RESERVED
    )
    with pytest.raises(InspectionPhotoLimitError):
        Inspection(InspectionStage.BEFORE, _photos(MAX_INSPECTION_PHOTOS)).ensure_photo_fits(
            RentalStatus.RESERVED
        )


def test_damages_are_limited() -> None:
    damages = tuple(
        Damage(uuid.uuid4(), DamageSide.LEFT, "scratch", NOW) for _ in range(MAX_DAMAGES)
    )

    with pytest.raises(InspectionDamageLimitError):
        Inspection(InspectionStage.BEFORE, damages=damages).ensure_damage_fits(
            RentalStatus.RESERVED
        )


def test_an_inspection_needs_the_minimum_of_photos() -> None:
    with pytest.raises(InspectionIncompleteError):
        Inspection(InspectionStage.BEFORE, _photos(MIN_INSPECTION_PHOTOS - 1)).ensure_submittable(
            RentalStatus.RESERVED
        )
    Inspection(InspectionStage.BEFORE, _photos(MIN_INSPECTION_PHOTOS)).ensure_submittable(
        RentalStatus.RESERVED
    )


@pytest.mark.parametrize("comment", ["dent\x00", "\u202edent", "zero\u200bwidth"])
def test_damage_comments_refuse_invisible_characters(comment: str) -> None:
    with pytest.raises(ValueError, match="control or format"):
        normalize_damage_comment(comment)


def test_damage_comments_are_squeezed() -> None:
    assert normalize_damage_comment("  scratch \n on   the door ") == "scratch on the door"


def test_a_command_needs_an_active_rental_a_free_one_and_the_inspection() -> None:
    ensure_command_allowed(_rental(), inspection_submitted=True, now=NOW)
    with pytest.raises(RentalTransitionNotAllowedError):
        ensure_command_allowed(_rental(status=RentalStatus.CANCELLED), True, NOW)
    with pytest.raises(RentalBusyError):
        ensure_command_allowed(_rental(command_until=NOW + timedelta(seconds=1)), True, NOW)
    with pytest.raises(InspectionRequiredError):
        ensure_command_allowed(_rental(), inspection_submitted=False, now=NOW)


RULES = CommandRules(max_distance_m=50.0, max_state_age=timedelta(minutes=5))


def test_the_boundary_distance_is_within_reach() -> None:
    ensure_near_car(NOW, 50.0, RULES, NOW)
    with pytest.raises(TooFarFromCarError):
        ensure_near_car(NOW, 50.01, RULES, NOW)


def test_a_car_without_fresh_telemetry_is_nowhere() -> None:
    with pytest.raises(CarLocationUnknownError):
        ensure_near_car(None, None, RULES, NOW)
    with pytest.raises(CarLocationUnknownError):
        ensure_near_car(NOW - timedelta(minutes=5, seconds=1), 1.0, RULES, NOW)
    ensure_near_car(NOW - timedelta(minutes=5), 1.0, RULES, NOW)
