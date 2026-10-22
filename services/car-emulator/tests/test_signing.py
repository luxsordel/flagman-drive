from datetime import UTC, datetime, timedelta

import pytest

from car_emulator.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SignatureError,
    sign,
    signed_headers,
    verify,
)

SECRET = b"test-secret-0123456789abcdef0123456789"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
METHOD = "POST"
TARGET = b"/cars/1/commands"
BODY = b'{"a":1}'


def _verify(
    timestamp: str | None,
    signature: str | None,
    *,
    method: str = METHOD,
    target: bytes = TARGET,
    body: bytes = BODY,
) -> None:
    verify(
        SECRET,
        timestamp_header=timestamp,
        signature_header=signature,
        method=method,
        target=target,
        body=body,
        now=NOW,
        tolerance_s=300,
    )


def _headers(now: datetime = NOW, secret: bytes = SECRET) -> tuple[str, str]:
    headers = signed_headers(secret, METHOD, TARGET, BODY, now)
    return headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER]


def test_known_vector() -> None:
    # Computed with `printf '1700000000\nPOST\n/x\n{}' | openssl dgst -sha256 -hmac secret`;
    # pins the wire format from ADR-0006 so the API side can be checked against the same vector.
    assert sign(b"secret", 1700000000, "post", b"/x", b"{}") == (
        "sha256=c5b469326eae314dd42fab4901a79eb16ee0b61f28902b35f0653c508d1084f4"
    )


def test_valid_signature_passes() -> None:
    _verify(*_headers())


@pytest.mark.parametrize("skew", [-300, 300])
def test_timestamp_at_tolerance_edge_passes(skew: int) -> None:
    _verify(*_headers(NOW + timedelta(seconds=skew)))


@pytest.mark.parametrize("skew", [-301, 301])
def test_timestamp_outside_tolerance_fails(skew: int) -> None:
    with pytest.raises(SignatureError):
        _verify(*_headers(NOW + timedelta(seconds=skew)))


def test_tampered_body_fails() -> None:
    with pytest.raises(SignatureError):
        _verify(*_headers(), body=b'{"a":2}')


def test_other_target_fails() -> None:
    with pytest.raises(SignatureError):
        _verify(*_headers(), target=b"/cars/2/commands")


def test_other_method_fails() -> None:
    with pytest.raises(SignatureError):
        _verify(*_headers(), method="GET")


def test_wrong_secret_fails() -> None:
    with pytest.raises(SignatureError):
        _verify(*_headers(secret=b"another-secret-0123456789abcdef0123"))


@pytest.mark.parametrize(
    ("timestamp", "signature"),
    [
        (None, "sha256=00"),
        (str(int(NOW.timestamp())), None),
        ("", "sha256=00"),
        ("-1761134400", "sha256=00"),
        ("1761134400.5", "sha256=00"),
        ("١٧٦١١٣٤٤٠٠", "sha256=00"),
        ("1" * 40, "sha256=00"),
    ],
)
def test_missing_or_malformed_headers_fail(timestamp: str | None, signature: str | None) -> None:
    with pytest.raises(SignatureError):
        _verify(timestamp, signature)
