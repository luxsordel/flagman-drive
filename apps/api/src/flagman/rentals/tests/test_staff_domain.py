import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from flagman.rentals.domain import (
    Adjustment,
    ChargeStatus,
    Refundable,
    Rental,
    RentalAdjustmentInvalidError,
    RentalCursor,
    RentalSettlementPendingError,
    RentalStatus,
    RentalTransition,
    RentalTransitionNotAllowedError,
    adjusted_total,
    apply_transition,
    ensure_adjustment_allowed,
    normalize_staff_text,
    plan_refunds,
    refund_key,
    rental_key_prefix,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

RENTAL = Rental(
    id=uuid.uuid4(),
    user_id=uuid.uuid4(),
    car_id=uuid.uuid4(),
    status=RentalStatus.PAID,
    tariff_version_id=uuid.uuid4(),
    reserved_at=NOW - timedelta(hours=1),
    free_wait_until=NOW - timedelta(minutes=45),
    expires_at=NOW - timedelta(minutes=15),
    trip_started_at=NOW - timedelta(minutes=50),
    ended_at=NOW - timedelta(minutes=10),
    cancel_reason=None,
    command_until=None,
    charge_status=ChargeStatus.PAID,
)


def _adjustment(amount: int, seq: int = 1) -> Adjustment:
    return Adjustment(
        id=uuid.uuid4(),
        rental_id=RENTAL.id,
        seq=seq,
        amount=amount,
        comment="comment",
        staff_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4(),
        created_at=NOW,
    )


@pytest.mark.parametrize("status", [RentalStatus.IN_TRIP, RentalStatus.PARKED])
def test_an_operator_force_finishes_a_trip_in_any_driving_status(status: RentalStatus) -> None:
    assert apply_transition(status, RentalTransition.FORCE_FINISH) is RentalStatus.FINISHED


@pytest.mark.parametrize(
    "status", [s for s in RentalStatus if s not in (RentalStatus.IN_TRIP, RentalStatus.PARKED)]
)
def test_nothing_but_a_trip_is_force_finished(status: RentalStatus) -> None:
    # A stuck reservation runs out by itself (docs/adr/0022).
    with pytest.raises(RentalTransitionNotAllowedError):
        apply_transition(status, RentalTransition.FORCE_FINISH)


def test_the_total_is_the_receipt_plus_every_adjustment() -> None:
    assert adjusted_total(10_00, [_adjustment(-3_00), _adjustment(5_50, seq=2)]) == 12_50
    assert adjusted_total(10_00, []) == 10_00


@pytest.mark.parametrize(
    ("status", "charge"),
    [
        (RentalStatus.PAID, ChargeStatus.PAID),
        (RentalStatus.PAYMENT_FAILED, ChargeStatus.FAILED),
        (RentalStatus.CANCELLED, ChargeStatus.PAID),
        (RentalStatus.CANCELLED, ChargeStatus.FAILED),
        # A free trip: nothing was ever charged.
        (RentalStatus.FINISHED, None),
    ],
)
def test_a_rental_whose_money_is_known_may_be_adjusted(
    status: RentalStatus, charge: ChargeStatus | None
) -> None:
    rental = replace(RENTAL, status=status, charge_status=charge)

    ensure_adjustment_allowed(rental, 10_00, [], 2_00)
    ensure_adjustment_allowed(rental, 10_00, [], -10_00)


@pytest.mark.parametrize(
    "rental",
    [
        replace(RENTAL, status=RentalStatus.FINISHED, charge_status=ChargeStatus.PENDING),
        replace(RENTAL, status=RentalStatus.CANCELLED, charge_status=ChargeStatus.PENDING),
    ],
)
def test_a_rental_settling_right_now_waits(rental: Rental) -> None:
    with pytest.raises(RentalSettlementPendingError):
        ensure_adjustment_allowed(rental, 10_00, [], 1_00)


@pytest.mark.parametrize(
    "status", [RentalStatus.RESERVED, RentalStatus.IN_TRIP, RentalStatus.PARKED]
)
def test_an_active_rental_is_not_adjusted(status: RentalStatus) -> None:
    rental = replace(RENTAL, status=status, charge_status=None)

    with pytest.raises(RentalTransitionNotAllowedError):
        ensure_adjustment_allowed(rental, None, [], 1_00)


def test_an_adjustment_neither_goes_below_zero_nor_changes_nothing() -> None:
    with pytest.raises(RentalAdjustmentInvalidError):
        ensure_adjustment_allowed(RENTAL, 10_00, [_adjustment(-4_00)], -6_01)
    with pytest.raises(RentalAdjustmentInvalidError):
        ensure_adjustment_allowed(RENTAL, 10_00, [], 0)


def test_a_rental_without_a_receipt_has_nothing_to_adjust() -> None:
    rental = replace(RENTAL, status=RentalStatus.CANCELLED, charge_status=None)

    with pytest.raises(RentalAdjustmentInvalidError):
        ensure_adjustment_allowed(rental, None, [], 1_00)


def test_a_refund_takes_from_the_newest_payment_first() -> None:
    debt, extra, capture = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sources = [Refundable(debt, 2_00), Refundable(extra, 0), Refundable(capture, 10_00)]

    assert plan_refunds(5_00, sources) == ((debt, 2_00), (capture, 3_00))
    assert plan_refunds(1_00, sources) == ((debt, 1_00),)
    assert plan_refunds(0, sources) == ()


def test_a_refund_above_what_was_taken_is_a_bug() -> None:
    with pytest.raises(ValueError, match="do not cover"):
        plan_refunds(3_00, [Refundable(uuid.uuid4(), 2_00)])


def test_each_refund_has_a_key_of_its_own_under_the_rental() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    keys = {refund_key(RENTAL.id, seq, op) for seq in (1, 2) for op in (first, second)}

    assert len(keys) == 4
    assert all(key.startswith(rental_key_prefix(RENTAL.id)) for key in keys)


def test_a_cursor_survives_the_round_trip() -> None:
    cursor = RentalCursor(NOW, uuid.uuid4())

    assert RentalCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("value", ["", "not-base64!", "MjAyNi0xMS0wMlQxMjowMDowMHxub3Bl"])
def test_a_broken_cursor_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        RentalCursor.decode(value)


def test_staff_text_is_squeezed_and_invisible_characters_are_refused() -> None:
    assert normalize_staff_text("  car  stuck\n at the gate ") == "car stuck at the gate"
    with pytest.raises(ValueError, match="control or format"):
        normalize_staff_text("ok" + chr(0x202E) + "gnorw")
