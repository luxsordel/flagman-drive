from datetime import UTC, datetime, timedelta

import pytest

from flagman.fleet.signing import SignatureError, SignedRequest, sign, verify

SECRET = b"s" * 40
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
TARGET = b"/fleet/telemetry"
BODY = b'{"a":1}'


def _request(
    at: datetime = NOW,
    *,
    secret: bytes = SECRET,
    method: str = "POST",
    target: bytes = TARGET,
    body: bytes = BODY,
) -> SignedRequest:
    """Signed for POST TARGET with BODY; the keywords change what is sent, not what is signed."""
    timestamp = int(at.timestamp())
    return SignedRequest(
        method=method,
        target=target,
        body=body,
        timestamp_header=str(timestamp),
        signature_header=sign(secret, timestamp, "POST", TARGET, BODY),
    )


def _verify(request: SignedRequest) -> None:
    verify(SECRET, request, now=NOW, tolerance_s=300)


def test_known_vector_of_the_emulator() -> None:
    # The vector of services/car-emulator/tests/test_signing.py: both sides sign the same bytes.
    assert sign(b"secret", 1700000000, "post", b"/x", b"{}") == (
        "sha256=c5b469326eae314dd42fab4901a79eb16ee0b61f28902b35f0653c508d1084f4"
    )


def test_valid_signature_passes() -> None:
    _verify(_request())


@pytest.mark.parametrize("skew", [-300, 300])
def test_timestamp_at_tolerance_edge_passes(skew: int) -> None:
    _verify(_request(NOW + timedelta(seconds=skew)))


@pytest.mark.parametrize("skew", [-301, 301])
def test_timestamp_outside_tolerance_fails(skew: int) -> None:
    with pytest.raises(SignatureError, match="tolerance"):
        _verify(_request(NOW + timedelta(seconds=skew)))


@pytest.mark.parametrize(
    "request_",
    [
        _request(body=b'{"a":2}'),
        _request(target=b"/fleet/telemetry?a=1"),
        _request(method="PUT"),
        _request(secret=b"o" * 40),
    ],
    ids=["body", "target", "method", "secret"],
)
def test_anything_changed_after_signing_fails(request_: SignedRequest) -> None:
    with pytest.raises(SignatureError, match="mismatch"):
        _verify(request_)


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
    request = SignedRequest(
        method="POST",
        target=TARGET,
        body=BODY,
        timestamp_header=timestamp,
        signature_header=signature,
    )
    with pytest.raises(SignatureError):
        _verify(request)
