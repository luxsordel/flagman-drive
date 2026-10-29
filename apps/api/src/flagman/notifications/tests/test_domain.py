import uuid
from datetime import timedelta

import pytest

from flagman.identity.domain import Language
from flagman.notifications.domain import (
    MAX_ATTEMPTS,
    PUSH_TOKEN_RE,
    Notification,
    NotificationCategory,
    NotificationCategoryMandatoryError,
    NotificationEvent,
    NotificationParamsInvalidError,
    NotificationPreferences,
    battery_crossed_low,
    render,
    retry_delay,
)

RENTAL = uuid.UUID("00000000-0000-0000-0000-000000000001")

EXAMPLES = [
    Notification(NotificationEvent.RESERVATION_EXPIRING, RENTAL, {"minutes_left": 5}),
    Notification(NotificationEvent.RESERVATION_EXPIRED, RENTAL, {"amount": 600}),
    Notification(NotificationEvent.TRIP_LOW_BATTERY, RENTAL, {"battery_pct": 14}),
    Notification(NotificationEvent.TRIP_FINISHED, RENTAL, {"total": 1250}),
    Notification(NotificationEvent.PAYMENT_FAILED, RENTAL, {"amount": 990}),
]


def test_every_event_has_a_template_in_both_languages() -> None:
    assert {n.event for n in EXAMPLES} == set(NotificationEvent)
    for notification in EXAMPLES:
        for language in Language:
            content = render(notification, language)
            assert content.title
            assert "{" not in content.body


def test_text_follows_the_client_language() -> None:
    finished = EXAMPLES[3]

    assert render(finished, Language.RU).body == "Стоимость: 12,50 BYN. Чек — в истории поездок."
    assert render(finished, Language.EN).body == (
        "Total: 12.50 BYN. The receipt is in your trip history."
    )


def test_an_expired_reservation_names_its_paid_wait() -> None:
    expired = EXAMPLES[1]

    assert render(expired, Language.RU).body == (
        "Время брони вышло. Платное ожидание: 6,00 BYN. Чек — в истории поездок."
    )
    assert render(expired, Language.EN).body == (
        "Your reservation time is up. Paid waiting: 6.00 BYN. The receipt is in your trip history."
    )


def test_a_free_expiry_mentions_no_money() -> None:
    expired = Notification(NotificationEvent.RESERVATION_EXPIRED, RENTAL, {"amount": 0})

    assert render(expired, Language.RU).body == "Время брони вышло, машина снова доступна другим."
    assert (
        render(expired, Language.EN).body == "Your reservation time is up; the car is free again."
    )


def test_an_expiry_without_its_amount_is_refused() -> None:
    with pytest.raises(NotificationParamsInvalidError):
        Notification(NotificationEvent.RESERVATION_EXPIRED, RENTAL, {})


@pytest.mark.parametrize(
    ("minutes", "ru", "en"),
    [
        (1, "1 минуту", "1 minute"),
        (2, "2 минуты", "2 minutes"),
        (5, "5 минут", "5 minutes"),
        (11, "11 минут", "11 minutes"),
        (21, "21 минуту", "21 minutes"),
        (24, "24 минуты", "24 minutes"),
    ],
)
def test_minutes_are_in_the_right_plural_form(minutes: int, ru: str, en: str) -> None:
    notification = Notification(
        NotificationEvent.RESERVATION_EXPIRING, RENTAL, {"minutes_left": minutes}
    )

    assert ru in render(notification, Language.RU).body
    assert en in render(notification, Language.EN).body


def test_payload_carries_ids_only() -> None:
    content = render(EXAMPLES[4], Language.RU)

    assert content.data == {"event": "payment_failed", "rental_id": str(RENTAL)}


@pytest.mark.parametrize(
    "params",
    [{}, {"minutes_left": 5, "extra": 1}, {"minutes_left": -1}, {"minutes_left": True}],
)
def test_wrong_params_are_a_bug_of_the_caller(params: dict[str, int]) -> None:
    with pytest.raises(NotificationParamsInvalidError):
        Notification(NotificationEvent.RESERVATION_EXPIRING, RENTAL, params)


def test_one_event_per_rental_shares_a_dedup_key() -> None:
    first = Notification(NotificationEvent.TRIP_LOW_BATTERY, RENTAL, {"battery_pct": 14})
    again = Notification(NotificationEvent.TRIP_LOW_BATTERY, RENTAL, {"battery_pct": 9})
    other_rental = Notification(
        NotificationEvent.TRIP_LOW_BATTERY, uuid.uuid4(), {"battery_pct": 9}
    )
    other_event = Notification(NotificationEvent.TRIP_FINISHED, RENTAL, {"total": 0})

    assert first.dedup_key == again.dedup_key
    assert first.dedup_key != other_rental.dedup_key
    assert first.dedup_key != other_event.dedup_key


@pytest.mark.parametrize(
    ("previous", "current", "crossed"),
    [
        (25, 19, True),
        (20, 19, True),
        (None, 10, True),
        (19, 15, False),
        (25, 20, False),
        (None, 50, False),
    ],
)
def test_low_battery_is_reported_when_the_threshold_is_crossed(
    previous: int | None, current: int, crossed: bool
) -> None:
    assert battery_crossed_low(previous, current, threshold_pct=20) is crossed


def test_retries_back_off_and_stop() -> None:
    delays = [retry_delay(attempts) for attempts in range(1, MAX_ATTEMPTS + 1)]

    assert delays == [
        timedelta(seconds=30),
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=4),
        None,
    ]


def test_trip_and_payment_notifications_cannot_be_switched_off() -> None:
    preferences = NotificationPreferences(frozenset())

    with pytest.raises(NotificationCategoryMandatoryError):
        preferences.with_changes({NotificationCategory.PAYMENT: False})
    with pytest.raises(NotificationCategoryMandatoryError):
        preferences.with_changes({NotificationCategory.TRIP: False})


def test_optional_categories_switch_off_and_on() -> None:
    off = NotificationPreferences(frozenset()).with_changes(
        {NotificationCategory.RESERVATION: False}
    )
    on = off.with_changes({NotificationCategory.RESERVATION: True})

    assert not off.is_enabled(NotificationCategory.RESERVATION)
    assert off.is_enabled(NotificationCategory.TRIP)
    assert on.is_enabled(NotificationCategory.RESERVATION)


@pytest.mark.parametrize(
    ("token", "valid"),
    [
        ("ExponentPushToken[abcDEF123_-]", True),
        ("ExpoPushToken[abc]", True),
        ("ExponentPushToken[]", False),
        ("ExponentPushToken[a b]", False),
        ("fcm:abc", False),
    ],
)
def test_only_expo_tokens_are_accepted(token: str, valid: bool) -> None:
    assert (PUSH_TOKEN_RE.fullmatch(token) is not None) is valid


def test_a_reservation_reminder_lives_until_the_reservation_ends() -> None:
    expiring = Notification(NotificationEvent.RESERVATION_EXPIRING, RENTAL, {"minutes_left": 5})

    assert expiring.lifetime == timedelta(minutes=5)


def test_every_event_has_a_lifetime() -> None:
    for notification in EXAMPLES:
        assert notification.lifetime > timedelta(0)
