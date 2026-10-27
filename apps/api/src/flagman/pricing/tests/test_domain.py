from datetime import UTC, datetime, timedelta

import pytest

from flagman.pricing.domain import (
    MAX_FREE_WAITING_MINUTES,
    MAX_HOLD_AMOUNT,
    MAX_PRICE_PER_MINUTE,
    Interval,
    IntervalKind,
    Quote,
    QuoteLine,
    TariffPrices,
    TariffPricesInvalidError,
    calculate,
)

START = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
PRICES = TariffPrices(
    driving_per_minute=39,
    parked_per_minute=10,
    waiting_per_minute=12,
    free_waiting_minutes=15,
    hold_amount=3000,
)


def _interval(kind: IntervalKind, start: timedelta, length: timedelta) -> Interval:
    return Interval(kind, START + start, START + start + length)


def _driving(seconds: float) -> Interval:
    return _interval(IntervalKind.DRIVING, timedelta(), timedelta(seconds=seconds))


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [(0, 0), (0.000001, 1), (1, 1), (59.999999, 1), (60, 1), (61, 2), (600, 10)],
)
def test_a_started_minute_is_billed_in_full(seconds: float, minutes: int) -> None:
    quote = calculate([_driving(seconds)], PRICES)

    assert quote == Quote(
        lines=(QuoteLine(IntervalKind.DRIVING, minutes, 0, 39, minutes * 39),), total=minutes * 39
    )


def test_time_is_summed_per_kind_before_rounding() -> None:
    # Three pieces of 20 seconds are one minute, not three.
    intervals = [
        _interval(IntervalKind.DRIVING, timedelta(seconds=0), timedelta(seconds=20)),
        _interval(IntervalKind.PARKED, timedelta(seconds=20), timedelta(seconds=20)),
        _interval(IntervalKind.DRIVING, timedelta(seconds=40), timedelta(seconds=20)),
        _interval(IntervalKind.DRIVING, timedelta(seconds=60), timedelta(seconds=20)),
    ]

    quote = calculate(intervals, PRICES)

    assert quote.lines == (
        QuoteLine(IntervalKind.DRIVING, 1, 0, 39, 39),
        QuoteLine(IntervalKind.PARKED, 1, 0, 10, 10),
    )
    assert quote.total == 49


@pytest.mark.parametrize(
    ("waited", "billed", "free"),
    [
        (timedelta(minutes=10), 0, 10),
        (timedelta(minutes=15), 0, 15),
        (timedelta(minutes=15, seconds=1), 1, 15),
        (timedelta(minutes=40), 25, 15),
    ],
)
def test_free_waiting_minutes_come_off_the_waiting_time(
    waited: timedelta, billed: int, free: int
) -> None:
    quote = calculate([_interval(IntervalKind.WAITING, timedelta(), waited)], PRICES)

    assert quote.lines == (QuoteLine(IntervalKind.WAITING, billed, free, 12, billed * 12),)


def test_free_minutes_apply_only_to_waiting() -> None:
    quote = calculate(
        [_driving(10 * 60), _interval(IntervalKind.PARKED, timedelta(minutes=10), _min(5))],
        PRICES,
    )

    assert [line.free_minutes for line in quote.lines] == [0, 0]
    assert quote.total == 10 * 39 + 5 * 10


def test_whole_rental_lines_come_in_receipt_order() -> None:
    intervals = [
        _interval(IntervalKind.PARKED, _min(50), _min(5)),
        _interval(IntervalKind.WAITING, _min(0), _min(20)),
        _interval(IntervalKind.DRIVING, _min(20), _min(30)),
    ]

    quote = calculate(intervals, PRICES)

    assert [line.kind for line in quote.lines] == [
        IntervalKind.WAITING,
        IntervalKind.DRIVING,
        IntervalKind.PARKED,
    ]
    assert quote.total == 5 * 12 + 30 * 39 + 5 * 10


def test_no_intervals_cost_nothing() -> None:
    assert calculate([], PRICES) == Quote(lines=(), total=0)


def test_overlapping_intervals_are_refused() -> None:
    with pytest.raises(ValueError, match="overlap"):
        calculate(
            [_driving(120), _interval(IntervalKind.PARKED, timedelta(seconds=60), _min(1))],
            PRICES,
        )


def test_touching_intervals_are_fine() -> None:
    quote = calculate([_driving(60), _interval(IntervalKind.PARKED, _min(1), _min(1))], PRICES)

    assert quote.total == 39 + 10


def test_interval_needs_aware_and_ordered_bounds() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Interval(IntervalKind.DRIVING, datetime(2026, 10, 26), START)  # noqa: DTZ001
    with pytest.raises(ValueError, match="before"):
        Interval(IntervalKind.DRIVING, START, START - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("driving_per_minute", -1),
        ("driving_per_minute", MAX_PRICE_PER_MINUTE + 1),
        ("parked_per_minute", -1),
        ("waiting_per_minute", MAX_PRICE_PER_MINUTE + 1),
        ("free_waiting_minutes", MAX_FREE_WAITING_MINUTES + 1),
        ("hold_amount", MAX_HOLD_AMOUNT + 1),
        ("hold_amount", True),
    ],
)
def test_prices_out_of_range_are_refused(field: str, value: int) -> None:
    values = {
        "driving_per_minute": 39,
        "parked_per_minute": 10,
        "waiting_per_minute": 12,
        "free_waiting_minutes": 15,
        "hold_amount": 3000,
        field: value,
    }

    with pytest.raises(TariffPricesInvalidError, match=field):
        TariffPrices(**values)


def test_minutes_may_be_free_but_the_hold_may_not() -> None:
    free = TariffPrices(0, 0, 0, 0, 1)

    assert calculate([_driving(600)], free).total == 0
    with pytest.raises(TariffPricesInvalidError, match="hold_amount"):
        TariffPrices(0, 0, 0, 0, 0)


def _min(minutes: int) -> timedelta:
    return timedelta(minutes=minutes)
