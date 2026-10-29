"""Push notifications to clients, see docs/adr/0020: what is sent, in which words, how often."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from uuid import UUID

from flagman.core.errors import AppError
from flagman.identity.domain import Language

# Expo push tokens look like `ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]`; the older SDKs used
# `ExpoPushToken[...]`. Anything else is not something Expo can deliver to.
PUSH_TOKEN_RE = re.compile(r"Expo(nent)?PushToken\[[A-Za-z0-9_-]{1,200}\]")
PUSH_TOKEN_MAX_LENGTH = 255

# A failed delivery is tried again after 30 s, 1 min, 2 min, 4 min: a provider outage of a few
# minutes still gets through, while a reminder that comes an hour late is no use to anyone.
MAX_ATTEMPTS = 5
FIRST_RETRY_DELAY = timedelta(seconds=30)


class PushPlatform(StrEnum):
    IOS = "ios"
    ANDROID = "android"


class NotificationCategory(StrEnum):
    RESERVATION = "reservation"
    TRIP = "trip"
    PAYMENT = "payment"


# What the client must know about money and the car they drive cannot be switched off.
MANDATORY_CATEGORIES = frozenset({NotificationCategory.TRIP, NotificationCategory.PAYMENT})


class NotificationEvent(StrEnum):
    RESERVATION_EXPIRING = "reservation_expiring"
    RESERVATION_EXPIRED = "reservation_expired"
    TRIP_LOW_BATTERY = "trip_low_battery"
    TRIP_FINISHED = "trip_finished"
    PAYMENT_FAILED = "payment_failed"


CATEGORY_OF = {
    NotificationEvent.RESERVATION_EXPIRING: NotificationCategory.RESERVATION,
    NotificationEvent.RESERVATION_EXPIRED: NotificationCategory.RESERVATION,
    NotificationEvent.TRIP_LOW_BATTERY: NotificationCategory.TRIP,
    NotificationEvent.TRIP_FINISHED: NotificationCategory.TRIP,
    NotificationEvent.PAYMENT_FAILED: NotificationCategory.PAYMENT,
}


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    # No registered device, or every attempt failed: nothing more will be tried.
    UNDELIVERED = "undelivered"


class NotificationCategoryMandatoryError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "notification-category-mandatory"
    title = "Notifications of this category cannot be switched off"


class NotificationParamsInvalidError(ValueError):
    """A caller passed parameters the event's template cannot use: a bug, not a client error."""


@dataclass(frozen=True)
class Notification:
    """One event for one client, as other modules hand it over.

    `params` holds only what the template needs: numbers, never names, phones or places.
    """

    event: NotificationEvent
    rental_id: UUID
    params: Mapping[str, int]

    def __post_init__(self) -> None:
        expected = _PARAMS[self.event]
        if set(self.params) != expected:
            raise NotificationParamsInvalidError(
                f"{self.event} takes {sorted(expected)}, got {sorted(self.params)}"
            )
        for name, value in self.params.items():
            # bool is an int too.
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise NotificationParamsInvalidError(f"{name} must be a non-negative int")

    @property
    def category(self) -> NotificationCategory:
        return CATEGORY_OF[self.event]

    @property
    def lifetime(self) -> timedelta:
        """How long after the event the notification is still worth a buzz: news of a
        reservation that is already over, delivered after a worker outage, only confuses."""
        if self.event is NotificationEvent.RESERVATION_EXPIRING:
            return timedelta(minutes=self.params["minutes_left"])
        return _LIFETIMES[self.event]

    @property
    def dedup_key(self) -> str:
        """Each event happens once per rental: a second call with the same key is dropped."""
        return f"{self.event}:{self.rental_id}"


_LIFETIMES = {
    NotificationEvent.RESERVATION_EXPIRED: timedelta(minutes=15),
    NotificationEvent.TRIP_LOW_BATTERY: timedelta(minutes=30),
    NotificationEvent.TRIP_FINISHED: timedelta(days=1),
    NotificationEvent.PAYMENT_FAILED: timedelta(days=1),
}

_PARAMS: dict[NotificationEvent, frozenset[str]] = {
    NotificationEvent.RESERVATION_EXPIRING: frozenset({"minutes_left"}),
    NotificationEvent.RESERVATION_EXPIRED: frozenset(),
    NotificationEvent.TRIP_LOW_BATTERY: frozenset({"battery_pct"}),
    NotificationEvent.TRIP_FINISHED: frozenset({"total"}),
    NotificationEvent.PAYMENT_FAILED: frozenset({"amount"}),
}


@dataclass(frozen=True)
class PushDevice:
    id: UUID
    user_id: UUID
    session_id: UUID
    token: str


@dataclass(frozen=True)
class OutboxItem:
    id: UUID
    user_id: UUID
    notification: Notification
    # Including the one about to be made.
    attempts: int
    expires_at: datetime


@dataclass(frozen=True)
class PushContent:
    title: str
    body: str
    # Read by the app when the notification is tapped; ids only.
    data: Mapping[str, str]


def _money(kopecks: int, language: Language) -> str:
    rubles, rest = divmod(kopecks, 100)
    separator = "," if language is Language.RU else "."
    return f"{rubles}{separator}{rest:02d} BYN"


def _minutes(n: int, language: Language) -> str:
    if language is Language.EN:
        return f"{n} minute" if n == 1 else f"{n} minutes"
    # Russian plural forms: 1, 21 минуту; 2-4, 22-24 минуты; 5-20, 11-14 минут.
    last, last_two = n % 10, n % 100
    if last == 1 and last_two != 11:  # noqa: PLR2004
        return f"{n} минуту"
    if last in {2, 3, 4} and last_two not in {12, 13, 14}:
        return f"{n} минуты"
    return f"{n} минут"


def _values(params: Mapping[str, int], language: Language) -> dict[str, str]:
    """Every parameter formatted for the language; a template uses only its own."""
    values = {name: str(value) for name, value in params.items()}
    if "minutes_left" in params:
        values["minutes_left"] = _minutes(params["minutes_left"], language)
    for money in ("total", "amount"):
        if money in params:
            values[money] = _money(params[money], language)
    return values


# (title, body) per event and language; the placeholders are the event's params.
_TEMPLATES: dict[NotificationEvent, dict[Language, tuple[str, str]]] = {
    NotificationEvent.RESERVATION_EXPIRING: {
        Language.RU: ("Бронь скоро истечёт", "Бронь закончится через {minutes_left}."),
        Language.EN: ("Reservation ends soon", "Your reservation ends in {minutes_left}."),
    },
    NotificationEvent.RESERVATION_EXPIRED: {
        Language.RU: ("Бронь истекла", "Время брони вышло, машина снова доступна другим."),
        Language.EN: ("Reservation expired", "Your reservation time is up; the car is free again."),
    },
    NotificationEvent.TRIP_LOW_BATTERY: {
        Language.RU: (
            "Низкий заряд",
            "Заряд машины {battery_pct}%. Спланируйте завершение поездки.",
        ),
        Language.EN: (
            "Low battery",
            "The car's battery is at {battery_pct}%. Plan where to end the trip.",
        ),
    },
    NotificationEvent.TRIP_FINISHED: {
        Language.RU: ("Поездка завершена", "Стоимость: {total}. Чек — в истории поездок."),
        Language.EN: ("Trip finished", "Total: {total}. The receipt is in your trip history."),
    },
    NotificationEvent.PAYMENT_FAILED: {
        Language.RU: ("Оплата не прошла", "Списать {amount} не удалось. Проверьте карту."),
        Language.EN: ("Payment failed", "We could not charge {amount}. Please check your card."),
    },
}


def render(notification: Notification, language: Language) -> PushContent:
    """The text is built here, on the server, in the client's language: the app sends nothing
    but its token."""
    title, template = _TEMPLATES[notification.event][language]
    body = template.format_map(_values(notification.params, language))
    return PushContent(
        title=title,
        body=body,
        data={"event": notification.event.value, "rental_id": str(notification.rental_id)},
    )


def battery_crossed_low(previous_pct: int | None, current_pct: int, threshold_pct: int) -> bool:
    """True when this reading is the one that went below the threshold.

    The first reading of a trip counts too: a car taken with little charge left still warns.
    Repeats within one trip are dropped by the dedup key anyway; this only saves the inserts.
    """
    below = current_pct < threshold_pct
    return below and (previous_pct is None or previous_pct >= threshold_pct)


def retry_delay(attempts: int) -> timedelta | None:
    """The pause before the next try after `attempts` failed ones; None when it is time to stop."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return FIRST_RETRY_DELAY * (1 << (attempts - 1))


@dataclass(frozen=True)
class NotificationPreferences:
    """Categories the client switched off; everything else is on by default."""

    disabled: frozenset[NotificationCategory]

    def is_enabled(self, category: NotificationCategory) -> bool:
        return category in MANDATORY_CATEGORIES or category not in self.disabled

    def with_changes(
        self, changes: Mapping[NotificationCategory, bool]
    ) -> "NotificationPreferences":
        disabled = set(self.disabled)
        for category, enabled in changes.items():
            if not enabled and category in MANDATORY_CATEGORIES:
                raise NotificationCategoryMandatoryError
            if enabled:
                disabled.discard(category)
            else:
                disabled.add(category)
        return NotificationPreferences(frozenset(disabled))
