"""HMAC request signing with a timestamp (docs/adr/0007).

Telematics and the fake payment provider (docs/adr/0019) sign alike. The same wire format as
services/car-emulator (car_emulator.signing): both sides test one vector.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

TIMESTAMP_HEADER = "X-Flagman-Timestamp"
SIGNATURE_HEADER = "X-Flagman-Signature"

_SCHEME = "sha256="
# Unix seconds fit in 10 digits until the year 2286; anything longer is garbage.
_MAX_TIMESTAMP_DIGITS = 10


class SignatureError(Exception):
    """The message is for the server log only: it says which check failed."""


def _message(timestamp: int, method: str, target: bytes, body: bytes) -> bytes:
    # Method and target are signed so a captured request cannot be replayed on another endpoint.
    return b"\n".join([str(timestamp).encode(), method.upper().encode(), target, body])


def sign(secret: bytes, timestamp: int, method: str, target: bytes, body: bytes) -> str:
    digest = hmac.new(secret, _message(timestamp, method, target, body), hashlib.sha256)
    return _SCHEME + digest.hexdigest()


@dataclass(frozen=True)
class SignedRequest:
    method: str
    # Path and query string, bytes as sent.
    target: bytes
    body: bytes
    timestamp_header: str | None
    signature_header: str | None


def verify(secret: bytes, request: SignedRequest, *, now: datetime, tolerance_s: int) -> None:
    timestamp_header = request.timestamp_header
    signature_header = request.signature_header
    if not timestamp_header or not signature_header:
        raise SignatureError("missing signature headers")
    if not timestamp_header.isascii() or not timestamp_header.isdigit():
        raise SignatureError("malformed timestamp")
    if len(timestamp_header) > _MAX_TIMESTAMP_DIGITS:
        raise SignatureError("malformed timestamp")
    timestamp = int(timestamp_header)
    if abs(now.timestamp() - timestamp) > tolerance_s:
        raise SignatureError("timestamp outside tolerance")
    expected = sign(secret, timestamp, request.method, request.target, request.body)
    if not hmac.compare_digest(expected.encode(), signature_header.encode()):
        raise SignatureError("signature mismatch")
