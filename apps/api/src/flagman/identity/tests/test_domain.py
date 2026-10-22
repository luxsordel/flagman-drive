import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from structlog.testing import capture_logs

from flagman.identity.domain import (
    ACCESS_TOKEN_TTL,
    CLIENT_AUDIENCE,
    JWT_ALGORITHM,
    JWT_ISSUER,
    OTP_MAX_ATTEMPTS,
    REFRESH_REUSE_GRACE,
    AccessClaims,
    AccessTokenInvalidError,
    OtpChallenge,
    OtpOutcome,
    RefreshDecision,
    RefreshTokenState,
    decide_refresh,
    generate_otp_code,
    generate_refresh_token,
    hash_otp_code,
    hash_refresh_token,
    issue_access_token,
    read_access_token,
)
from flagman.identity.ports import LogSmsSender, mask_phone

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
PHONE = "+375291234567"
PEPPER = "p" * 32
# 64 bytes: the forged HS512 token below would otherwise trigger PyJWT's short key warning.
SECRET = "s" * 64


def test_otp_codes_are_six_digits_with_leading_zeros_kept() -> None:
    codes = {generate_otp_code() for _ in range(2000)}

    assert all(len(code) == 6 and code.isdigit() for code in codes)
    # 2000 draws from a million: a broken generator (constant, short range) collides a lot.
    assert len(codes) > 1990
    assert any(code.startswith("0") for code in codes)


def test_otp_hash_depends_on_phone_and_pepper() -> None:
    code_hash = hash_otp_code(PHONE, "123456", PEPPER)

    assert "123456" not in code_hash
    assert code_hash != hash_otp_code("+375297654321", "123456", PEPPER)
    assert code_hash != hash_otp_code(PHONE, "123456", "q" * 32)


def _challenge(attempts: int = 0, expires_in: timedelta = timedelta(minutes=5)) -> OtpChallenge:
    return OtpChallenge(
        code_hash=hash_otp_code(PHONE, "123456", PEPPER),
        expires_at=NOW + expires_in,
        attempts=attempts,
    )


def _check(challenge: OtpChallenge, code: str, now: datetime = NOW) -> tuple[OtpOutcome, int]:
    result = challenge.check(hash_otp_code(PHONE, code, PEPPER), now)
    return result.outcome, result.attempts


def test_right_code_is_accepted() -> None:
    assert _check(_challenge(), "123456") == (OtpOutcome.ACCEPTED, 1)


def test_wrong_code_counts_the_attempt() -> None:
    assert _check(_challenge(attempts=2), "000000") == (OtpOutcome.WRONG, 3)


def test_last_wrong_attempt_expires_the_code() -> None:
    challenge = _challenge(attempts=OTP_MAX_ATTEMPTS - 1)

    assert _check(challenge, "000000") == (OtpOutcome.EXPIRED, OTP_MAX_ATTEMPTS)


def test_right_code_after_all_attempts_is_rejected() -> None:
    challenge = _challenge(attempts=OTP_MAX_ATTEMPTS)

    assert _check(challenge, "123456") == (OtpOutcome.EXPIRED, OTP_MAX_ATTEMPTS)


@pytest.mark.parametrize("late_by", [timedelta(0), timedelta(seconds=1)])
def test_right_code_after_expiry_is_rejected(late_by: timedelta) -> None:
    challenge = _challenge(expires_in=timedelta(minutes=5))

    outcome, _ = _check(challenge, "123456", now=NOW + timedelta(minutes=5) + late_by)

    assert outcome is OtpOutcome.EXPIRED


def test_refresh_tokens_are_random_and_hashed() -> None:
    first, second = generate_refresh_token(), generate_refresh_token()

    assert first != second
    assert len(first) >= 43  # 32 bytes in base64url
    assert hash_refresh_token(first) != first
    assert len(hash_refresh_token(first)) == 64


def _state(
    *,
    used_ago: timedelta | None = None,
    revoked: bool = False,
    expires_in: timedelta = timedelta(days=1),
) -> RefreshTokenState:
    return RefreshTokenState(
        used_at=None if used_ago is None else NOW - used_ago,
        session_revoked=revoked,
        session_expires_at=NOW + expires_in,
    )


@pytest.mark.parametrize(
    ("state", "decision"),
    [
        (None, RefreshDecision.REJECT),
        (_state(), RefreshDecision.ROTATE),
        (_state(used_ago=timedelta(0)), RefreshDecision.ALREADY_ROTATED),
        (
            _state(used_ago=REFRESH_REUSE_GRACE - timedelta(seconds=1)),
            RefreshDecision.ALREADY_ROTATED,
        ),
        (_state(used_ago=REFRESH_REUSE_GRACE), RefreshDecision.REUSE_DETECTED),
        (_state(used_ago=timedelta(days=1)), RefreshDecision.REUSE_DETECTED),
        # Reuse wins over revocation: a stolen old token still burns the device's new sessions,
        # and a revoked session gets no grace, it has nothing left to retry for.
        (_state(used_ago=timedelta(days=1), revoked=True), RefreshDecision.REUSE_DETECTED),
        (_state(used_ago=timedelta(0), revoked=True), RefreshDecision.REUSE_DETECTED),
        (_state(revoked=True), RefreshDecision.REJECT),
        (_state(expires_in=timedelta(0)), RefreshDecision.REJECT),
    ],
)
def test_refresh_decision(state: RefreshTokenState | None, decision: RefreshDecision) -> None:
    assert decide_refresh(state, NOW) is decision


def _claims() -> AccessClaims:
    return AccessClaims(user_id=uuid.uuid4(), session_id=uuid.uuid4())


def test_access_token_round_trip() -> None:
    claims = _claims()

    token = issue_access_token(claims, SECRET, datetime.now(UTC))

    assert read_access_token(token, SECRET) == claims


def test_access_token_lives_fifteen_minutes() -> None:
    token = issue_access_token(_claims(), SECRET, NOW)

    payload = jwt.decode(token, options={"verify_signature": False})

    assert payload["exp"] - payload["iat"] == ACCESS_TOKEN_TTL.total_seconds() == 900


def test_expired_access_token_is_rejected() -> None:
    token = issue_access_token(_claims(), SECRET, datetime.now(UTC) - ACCESS_TOKEN_TTL)

    with pytest.raises(AccessTokenInvalidError):
        read_access_token(token, SECRET)


def test_access_token_signed_with_another_secret_is_rejected() -> None:
    token = issue_access_token(_claims(), "x" * 32, datetime.now(UTC))

    with pytest.raises(AccessTokenInvalidError):
        read_access_token(token, SECRET)


def _forged(**overrides: object) -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    payload: dict[str, object] = {
        "iss": JWT_ISSUER,
        "aud": CLIENT_AUDIENCE,
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 60,
    }
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value is not None}


@pytest.mark.parametrize(
    "token",
    [
        # Staff token (#32) or any other audience.
        jwt.encode(_forged(aud="staff"), SECRET, algorithm=JWT_ALGORITHM),
        jwt.encode(_forged(iss="someone-else"), SECRET, algorithm=JWT_ALGORITHM),
        jwt.encode(_forged(sid=None), SECRET, algorithm=JWT_ALGORITHM),
        jwt.encode(_forged(sub="not-a-uuid"), SECRET, algorithm=JWT_ALGORITHM),
        # alg=none: no signature at all.
        jwt.encode(_forged(), None, algorithm="none"),
        jwt.encode(_forged(), SECRET, algorithm="HS512"),
        "not.a.jwt",
        "",
    ],
)
def test_forged_access_tokens_are_rejected(token: str) -> None:
    with pytest.raises(AccessTokenInvalidError):
        read_access_token(token, SECRET)


def test_phone_is_masked() -> None:
    assert mask_phone(PHONE) == "+375 29 *** ** 67"


async def test_log_sms_sender_never_logs_the_full_phone() -> None:
    with capture_logs() as logs:
        await LogSmsSender().send_otp(PHONE, "123456")

    assert logs == [
        {
            "event": "otp_sms_stub",
            "phone": "+375 29 *** ** 67",
            "code": "123456",
            "log_level": "info",
        }
    ]
