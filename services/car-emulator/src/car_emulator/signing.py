"""HMAC request signing shared by commands and telemetry webhooks (see docs/adr/0006)."""

import hashlib
import hmac
from datetime import datetime

TIMESTAMP_HEADER = "X-Flagman-Timestamp"
SIGNATURE_HEADER = "X-Flagman-Signature"

_SCHEME = "sha256="
# Unix seconds fit in 10 digits until the year 2286; anything longer is garbage.
_MAX_TIMESTAMP_DIGITS = 10


class SignatureError(Exception):
    pass


def _message(timestamp: int, method: str, target: bytes, body: bytes) -> bytes:
    # Method and target are signed so a captured request cannot be replayed against another car.
    return b"\n".join([str(timestamp).encode(), method.upper().encode(), target, body])


def sign(secret: bytes, timestamp: int, method: str, target: bytes, body: bytes) -> str:
    digest = hmac.new(secret, _message(timestamp, method, target, body), hashlib.sha256)
    return _SCHEME + digest.hexdigest()


def signed_headers(
    secret: bytes, method: str, target: bytes, body: bytes, now: datetime
) -> dict[str, str]:
    timestamp = int(now.timestamp())
    return {
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret, timestamp, method, target, body),
    }


def verify(
    secret: bytes,
    *,
    timestamp_header: str | None,
    signature_header: str | None,
    method: str,
    target: bytes,
    body: bytes,
    now: datetime,
    tolerance_s: int,
) -> None:
    if not timestamp_header or not signature_header:
        raise SignatureError("missing signature headers")
    if not timestamp_header.isascii() or not timestamp_header.isdigit():
        raise SignatureError("malformed timestamp")
    if len(timestamp_header) > _MAX_TIMESTAMP_DIGITS:
        raise SignatureError("malformed timestamp")
    timestamp = int(timestamp_header)
    if abs(now.timestamp() - timestamp) > tolerance_s:
        raise SignatureError("timestamp outside tolerance")
    expected = sign(secret, timestamp, method, target, body)
    if not hmac.compare_digest(expected.encode(), signature_header.encode()):
        raise SignatureError("signature mismatch")
