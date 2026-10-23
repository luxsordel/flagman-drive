from datetime import date

import pytest

from flagman.identity.domain import (
    BIRTH_DATE_MAX_AGE_YEARS,
    NAME_MAX_LENGTH,
    BirthDateInvalidError,
    check_birth_date,
    normalize_name,
)

TODAY = date(2026, 10, 23)


def test_name_is_trimmed() -> None:
    assert normalize_name("  Анна ") == "Анна"


@pytest.mark.parametrize("name", ["Мария-Луиза", "O'Brien", "Jean Paul", "Ёлкина"])
def test_ordinary_names_pass(name: str) -> None:
    assert normalize_name(name) == name


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_empty_name_is_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_name(name)


def test_name_length_limit() -> None:
    assert normalize_name("x" * NAME_MAX_LENGTH) == "x" * NAME_MAX_LENGTH
    with pytest.raises(ValueError, match="at most"):
        normalize_name("x" * (NAME_MAX_LENGTH + 1))


def test_name_length_is_counted_after_trimming() -> None:
    assert normalize_name("  " + "x" * NAME_MAX_LENGTH + " ") == "x" * NAME_MAX_LENGTH


def test_combining_marks_are_joined() -> None:
    # "E" + COMBINING DIAERESIS looks exactly like the single letter U+00CB.
    assert normalize_name(f"E{chr(0x0308)}lkina") == f"{chr(0x00CB)}lkina"


@pytest.mark.parametrize(
    "name",
    [
        f"Jean{chr(0x00A0)}Paul",  # no-break space
        f"Jean{chr(0x2009)}Paul",  # thin space
        "Jean   Paul",
    ],
)
def test_spaces_inside_become_one_plain_space(name: str) -> None:
    assert normalize_name(name) == "Jean Paul"


@pytest.mark.parametrize(
    "name",
    [
        f"An{chr(0x00)}na",  # NUL
        f"An{chr(0x0A)}na",  # newline inside
        f"An{chr(0x200B)}na",  # zero-width space
        f"Anna{chr(0x202E)}",  # right-to-left override
        f"An{chr(0x2028)}na",  # line separator
        f"An{chr(0x2029)}na",  # paragraph separator
    ],
)
def test_invisible_characters_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="control, format or line break"):
        normalize_name(name)


def test_birth_date_today_and_in_the_past_pass() -> None:
    check_birth_date(TODAY, TODAY)
    check_birth_date(date(1990, 5, 17), TODAY)


def test_birth_date_in_the_future_is_rejected() -> None:
    with pytest.raises(BirthDateInvalidError, match="future"):
        check_birth_date(date(2026, 10, 24), TODAY)


def test_birth_date_age_limit() -> None:
    earliest = date(TODAY.year - BIRTH_DATE_MAX_AGE_YEARS, 1, 1)
    check_birth_date(earliest, TODAY)
    with pytest.raises(BirthDateInvalidError, match=f"before {earliest.isoformat()}"):
        check_birth_date(date(earliest.year - 1, 12, 31), TODAY)
