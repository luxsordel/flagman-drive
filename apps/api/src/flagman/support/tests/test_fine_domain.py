import uuid
from datetime import UTC, datetime

import pytest

from flagman.support.domain import (
    FineCursor,
    FineReasonRequiredError,
    FineStatus,
    FineTransition,
    FineTransitionNotAllowedError,
    apply_fine_transition,
    ensure_reason,
    fine_charge_key,
    fine_refund_key,
    is_low_battery_end,
)


@pytest.mark.parametrize(
    ("battery_pct", "threshold_pct", "fined"),
    [
        (9, 10, True),
        (0, 10, True),
        (10, 10, False),
        (80, 10, False),
        # No telemetry at the end: a client is not fined on a guess.
        (None, 10, False),
        # A zero threshold turns the fine off.
        (0, 0, False),
    ],
)
def test_low_battery_fine_is_below_the_threshold_only(
    battery_pct: int | None, threshold_pct: int, fined: bool
) -> None:
    assert is_low_battery_end(battery_pct, threshold_pct) is fined


@pytest.mark.parametrize(
    ("current", "transition", "target"),
    [
        (FineStatus.PENDING, FineTransition.CHARGE_SUCCEEDED, FineStatus.PAID),
        (FineStatus.UNPAID, FineTransition.CHARGE_SUCCEEDED, FineStatus.PAID),
        (FineStatus.PENDING, FineTransition.CHARGE_DECLINED, FineStatus.UNPAID),
        (FineStatus.UNPAID, FineTransition.CHARGE_DECLINED, FineStatus.UNPAID),
        (FineStatus.PENDING, FineTransition.CANCEL, FineStatus.CANCELLED),
        (FineStatus.UNPAID, FineTransition.CANCEL, FineStatus.CANCELLED),
        (FineStatus.PAID, FineTransition.CANCEL, FineStatus.CANCELLED),
    ],
)
def test_allowed_fine_transitions(
    current: FineStatus, transition: FineTransition, target: FineStatus
) -> None:
    assert apply_fine_transition(current, transition) is target


@pytest.mark.parametrize(
    ("current", "transition"),
    [
        # A paid fine is not charged again, a cancelled one never comes back.
        (FineStatus.PAID, FineTransition.CHARGE_SUCCEEDED),
        (FineStatus.PAID, FineTransition.CHARGE_DECLINED),
        (FineStatus.CANCELLED, FineTransition.CHARGE_SUCCEEDED),
        (FineStatus.CANCELLED, FineTransition.CHARGE_DECLINED),
        (FineStatus.CANCELLED, FineTransition.CANCEL),
    ],
)
def test_forbidden_fine_transitions(current: FineStatus, transition: FineTransition) -> None:
    with pytest.raises(FineTransitionNotAllowedError):
        apply_fine_transition(current, transition)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_a_reason_is_required(text: str | None) -> None:
    with pytest.raises(FineReasonRequiredError):
        ensure_reason(text)


def test_a_reason_is_trimmed() -> None:
    assert ensure_reason("  Smoked  ") == "Smoked"


def test_charge_and_refund_keys_never_collide() -> None:
    fine_id = uuid.uuid4()

    keys = {fine_charge_key(fine_id, 1), fine_charge_key(fine_id, 2), fine_refund_key(fine_id, 1)}

    assert len(keys) == 3
    assert all(key.startswith(f"fine:{fine_id}:") for key in keys)


def test_the_cursor_survives_a_round_trip() -> None:
    cursor = FineCursor(datetime(2026, 11, 3, 12, tzinfo=UTC), uuid.uuid4())

    assert FineCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("value", ["", "not-base64!", "MjAyNg"])
def test_a_broken_cursor_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        FineCursor.decode(value)
