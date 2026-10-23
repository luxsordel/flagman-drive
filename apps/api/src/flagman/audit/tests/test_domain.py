import uuid
from datetime import UTC, datetime

import pytest

from flagman.audit.domain import (
    DETAILS_MAX_KEYS,
    DETAILS_MAX_STRING_LENGTH,
    Actor,
    ActorType,
    Cursor,
    validate_details,
)


def test_details_keep_simple_values_and_turn_ids_into_strings() -> None:
    document_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")

    checked = validate_details(
        {"reason": "blurry photo", "attempt": 2, "approved": False, "document_id": document_id}
    )

    assert checked == {
        "reason": "blurry photo",
        "attempt": 2,
        "approved": False,
        "document_id": str(document_id),
    }


@pytest.mark.parametrize(
    "value",
    ["+375291234567", "call 375291234567", "client@example.com", "write to a@b.by please"],
)
def test_details_reject_phones_and_emails(value: str) -> None:
    with pytest.raises(ValueError, match="looks like a phone number or an email") as error:
        validate_details({"comment": value})

    assert value not in str(error.value)


@pytest.mark.parametrize("value", [375291234567, -375291234567, 291234567])
def test_details_reject_phones_passed_as_numbers(value: int) -> None:
    with pytest.raises(ValueError, match="looks like a phone number") as error:
        validate_details({"phone": value})

    assert str(value) not in str(error.value)


@pytest.mark.parametrize("value", [True, 0, -1, 12345678])
def test_details_keep_short_numbers_and_flags(value: int) -> None:
    assert validate_details({"count": value}) == {"count": value}


def test_details_reject_too_long_strings() -> None:
    with pytest.raises(ValueError, match="longer than"):
        validate_details({"comment": "x" * (DETAILS_MAX_STRING_LENGTH + 1)})


def test_details_reject_too_many_keys() -> None:
    with pytest.raises(ValueError, match="at most"):
        validate_details({f"key_{index}": index for index in range(DETAILS_MAX_KEYS + 1)})


@pytest.mark.parametrize("key", ["Reason", "1st", "with space", "", "x" * 65, "reason\n"])
def test_details_reject_keys_that_are_not_snake_case(key: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        validate_details({key: 1})


def test_details_reject_nested_values() -> None:
    with pytest.raises(TypeError, match="must be str, int, bool, UUID or None"):
        validate_details({"nested": {"phone": "x"}})  # type: ignore[dict-item]


def test_system_actor_has_no_id_and_others_must_have_one() -> None:
    assert Actor(ActorType.SYSTEM).id is None
    with pytest.raises(ValueError, match="system actor"):
        Actor(ActorType.SYSTEM, uuid.uuid4())
    with pytest.raises(ValueError, match="system actor"):
        Actor(ActorType.STAFF)


def test_cursor_round_trip() -> None:
    cursor = Cursor(datetime(2026, 10, 23, 12, 30, 1, 123456, tzinfo=UTC), uuid.uuid4())

    assert Cursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize(
    "value",
    [
        "not base64 at all!",
        "bm8gc2VwYXJhdG9y",  # "no separator"
        Cursor(datetime(2026, 10, 23, tzinfo=UTC), uuid.uuid4()).encode()[:-4],
        # A naive time would compare as a different moment depending on the server zone.
        "MjAyNi0xMC0yM1QxMjowMDowMHwxMjNlNDU2Ny1lODliLTEyZDMtYTQ1Ni00MjY2MTQxNzQwMDA",
    ],
)
def test_cursor_rejects_garbage(value: str) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        Cursor.decode(value)
