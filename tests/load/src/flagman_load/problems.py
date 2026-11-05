"""Reads the API's errors (RFC 9457). Only the slug: titles and details may echo personal data."""

from requests import Response

_TYPE_PREFIX = "urn:flagman:problem:"


def error_code(response: Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    kind = body.get("type", "") if isinstance(body, dict) else ""
    return str(kind).removeprefix(_TYPE_PREFIX)
