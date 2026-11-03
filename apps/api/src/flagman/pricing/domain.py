"""Tariffs, fines, their versions and the price of a rental. No I/O here, see docs/adr/0018."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from itertools import pairwise
from uuid import UUID

from flagman.core.errors import AppError

DEFAULT_FREE_WAITING_MINUTES = 15
# Typo guards, far above any real price: 1000 BYN a minute, a ten-hour free reservation and a
# 10 000 BYN hold all mean a lost or extra digit.
MAX_PRICE_PER_MINUTE = 100_000
MAX_FREE_WAITING_MINUTES = 600
MAX_HOLD_AMOUNT = 1_000_000

_MINUTE = timedelta(minutes=1)


class IntervalKind(StrEnum):
    """Values are stored as is: renaming one needs a migration. Also the order of receipt lines."""

    # A reserved car waiting for the client: free at first, then paid.
    WAITING = "waiting"
    DRIVING = "driving"
    PARKED = "parked"


class TariffNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "tariff-not-found"
    title = "Tariff not found"


class TariffVersionNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "tariff-version-not-found"
    title = "Tariff version not found"


class CarModelNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "car-model-not-found"
    title = "Car model not found"


class TariffExistsError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "tariff-exists"
    title = "The car model has a tariff already, change it instead"


class TariffChangedError(AppError):
    """Another admin saved a version after the one the form was filled from."""

    status = HTTPStatus.CONFLICT
    slug = "tariff-changed"
    title = "The tariff was changed by someone else, reload it"


class TariffPricesInvalidError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "tariff-prices-invalid"
    title = "A tariff price is out of range"


@dataclass(frozen=True)
class TariffPrices:
    """All amounts in kopecks of BYN."""

    driving_per_minute: int
    parked_per_minute: int
    waiting_per_minute: int
    free_waiting_minutes: int
    hold_amount: int

    def __post_init__(self) -> None:
        limits = {
            "driving_per_minute": MAX_PRICE_PER_MINUTE,
            "parked_per_minute": MAX_PRICE_PER_MINUTE,
            "waiting_per_minute": MAX_PRICE_PER_MINUTE,
            "free_waiting_minutes": MAX_FREE_WAITING_MINUTES,
            "hold_amount": MAX_HOLD_AMOUNT,
        }
        for name, limit in limits.items():
            value = getattr(self, name)
            # A payment provider holds a positive amount only; every other price may be free.
            least = 1 if name == "hold_amount" else 0
            # bool is an int too, and True would pass as one kopeck.
            if type(value) is not int or not least <= value <= limit:
                raise TariffPricesInvalidError(f"{name} must be within {least}..{limit}")

    def per_minute(self, kind: IntervalKind) -> int:
        match kind:
            case IntervalKind.WAITING:
                return self.waiting_per_minute
            case IntervalKind.DRIVING:
                return self.driving_per_minute
            case IntervalKind.PARKED:
                return self.parked_per_minute


@dataclass(frozen=True)
class TariffVersion:
    """Never changes once saved: rentals keep pointing at the version they were booked with."""

    id: UUID
    tariff_id: UUID
    model_id: UUID
    number: int
    prices: TariffPrices
    valid_from: datetime
    # None for the demo seed.
    created_by: UUID | None


@dataclass(frozen=True)
class CarModelRef:
    id: UUID
    brand: str
    name: str


@dataclass(frozen=True)
class ModelTariff:
    """A row of the tariffs list: every car model, with or without a tariff."""

    model: CarModelRef
    current: TariffVersion | None


@dataclass(frozen=True)
class Interval:
    kind: IntervalKind
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("interval bounds must be timezone-aware")
        if self.end < self.start:
            raise ValueError("an interval cannot end before it starts")

    @property
    def length(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class QuoteLine:
    kind: IntervalKind
    # Billed minutes, after the free ones; a started minute counts in full.
    minutes: int
    # Only waiting has free minutes: the ones the rental used, not more than it waited.
    free_minutes: int
    per_minute: int
    amount: int


@dataclass(frozen=True)
class Quote:
    lines: tuple[QuoteLine, ...]
    total: int


def calculate(intervals: Iterable[Interval], prices: TariffPrices) -> Quote:
    """The price of a rental, one receipt line per kind of interval it had.

    Time is summed per kind first and rounded once, up to a whole minute: a started minute is
    billed in full, and splitting a trip into many short pieces does not round each piece up.
    The free waiting minutes come off the waiting time before rounding. Overlapping intervals are
    a bug of the caller and raise ValueError.
    """
    ordered = sorted(intervals, key=lambda interval: (interval.start, interval.end))
    for previous, current in pairwise(ordered):
        if current.start < previous.end:
            raise ValueError("rental intervals overlap")

    spent: dict[IntervalKind, timedelta] = {}
    for interval in ordered:
        spent[interval.kind] = spent.get(interval.kind, timedelta()) + interval.length

    lines = []
    for kind in IntervalKind:
        if kind not in spent:
            continue
        length = spent[kind]
        free = timedelta()
        if kind is IntervalKind.WAITING:
            free = min(length, prices.free_waiting_minutes * _MINUTE)
        minutes = _minutes_up(length - free)
        per_minute = prices.per_minute(kind)
        lines.append(QuoteLine(kind, minutes, _minutes_up(free), per_minute, minutes * per_minute))
    return Quote(lines=tuple(lines), total=sum(line.amount for line in lines))


def _minutes_up(length: timedelta) -> int:
    # Integer arithmetic on timedelta: no float rounding at the minute boundary.
    return -(-length // _MINUTE)


# Fines (docs/01, «Тарифы»). A typo guard like the prices above: a 10 000 BYN fine is a lost digit.
MAX_FINE_AMOUNT = 1_000_000
MAX_FINE_NAME_LENGTH = 100
# The fine the system charges on its own when a trip ends with a nearly empty battery; every other
# type is assigned by an operator.
LOW_BATTERY_FINE_CODE = "low_battery"
_AUTOMATIC_FINE_CODES = frozenset({LOW_BATTERY_FINE_CODE})
_FINE_CODE = re.compile(r"[a-z][a-z0-9_]{1,39}")


class FineKind(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class FineTypeNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "fine-type-not-found"
    title = "Fine type not found"


class FineTypeVersionNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "fine-type-version-not-found"
    title = "Fine type version not found"


class FineTypeExistsError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "fine-type-exists"
    title = "A fine type with this code exists already"


class FineTypeChangedError(AppError):
    """Another admin saved a version after the one the form was filled from."""

    status = HTTPStatus.CONFLICT
    slug = "fine-type-changed"
    title = "The fine type was changed by someone else, reload it"


class FineTermsInvalidError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "fine-terms-invalid"
    title = "A fine name or amount is out of range"


def fine_kind(code: str) -> FineKind:
    """Only the codes the system knows how to detect are automatic; an admin adds manual ones."""
    return FineKind.AUTOMATIC if code in _AUTOMATIC_FINE_CODES else FineKind.MANUAL


def ensure_fine_code(code: str) -> str:
    if not _FINE_CODE.fullmatch(code):
        raise FineTermsInvalidError("code must be 2..40 of a-z, 0-9 and _, starting with a letter")
    return code


@dataclass(frozen=True)
class FineTerms:
    """What a version of a fine type says: its names and its amount in kopecks of BYN."""

    name_ru: str
    name_en: str
    amount: int

    def __post_init__(self) -> None:
        for name in (self.name_ru, self.name_en):
            if not name.strip() or len(name) > MAX_FINE_NAME_LENGTH or name != name.strip():
                raise FineTermsInvalidError(
                    f"names must be 1..{MAX_FINE_NAME_LENGTH} characters without outer spaces"
                )
        # bool is an int too, and True would pass as one kopeck.
        if type(self.amount) is not int or not 1 <= self.amount <= MAX_FINE_AMOUNT:
            raise FineTermsInvalidError(f"amount must be within 1..{MAX_FINE_AMOUNT}")


@dataclass(frozen=True)
class FineTypeVersion:
    """Never changes once saved: a charged fine keeps pointing at the version it was charged by."""

    id: UUID
    fine_type_id: UUID
    code: str
    number: int
    terms: FineTerms
    valid_from: datetime
    # None for the demo seed.
    created_by: UUID | None

    @property
    def kind(self) -> FineKind:
        return fine_kind(self.code)
