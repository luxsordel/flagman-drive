"""Calendar dates in request bodies."""

import re

_DATE_STRING = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def require_date_string(value: object) -> object:
    """Use as `BeforeValidator(require_date_string)` on a `date` field.

    Pydantic would also read a number as a Unix timestamp and cut a datetime string down to its
    date; a calendar date is only ever `YYYY-MM-DD`. Not `strict=True`: FastAPI validates the
    parsed body, where strict refuses strings too.
    """
    if not isinstance(value, str) or not _DATE_STRING.fullmatch(value):
        raise ValueError("date must be a YYYY-MM-DD string")
    return value
