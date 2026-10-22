"""Sign-in rules: one-time codes, access tokens and refresh token rotation. No I/O here."""

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from uuid import UUID

import jwt

from flagman.core.errors import AppError

OTP_LENGTH = 6
OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5

ACCESS_TOKEN_TTL = timedelta(minutes=15)
# Sliding: every rotation moves the end of the session, so an app in use never signs out.
REFRESH_TOKEN_TTL = timedelta(days=30)

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "flagman"
# Staff tokens (#32) get their own audience, so they are never accepted where a client is expected.
CLIENT_AUDIENCE = "client"


class OtpInvalidError(AppError):
    status = HTTPStatus.BAD_REQUEST
    slug = "otp-invalid"
    title = "Invalid code"


class OtpExpiredError(AppError):
    """The code expired, ran out of attempts or was never requested: the client asks for a new one.

    These cases are not told apart, so the response does not reveal whether a code was sent.
    """

    status = HTTPStatus.BAD_REQUEST
    slug = "otp-expired"
    title = "Code expired"


class RefreshTokenInvalidError(AppError):
    """Unknown, expired, revoked or reused refresh token: the client signs in again."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "refresh-token-invalid"
    title = "Refresh token is invalid"


class AccessTokenInvalidError(AppError):
    """Missing, malformed, expired or foreign access token: the client refreshes or signs in."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "access-token-invalid"
    title = "Access token is invalid"

    @property
    def headers(self) -> Mapping[str, str] | None:
        return {"WWW-Authenticate": "Bearer"}


def keyed_hash(value: str, pepper: str) -> str:
    """HMAC-SHA256 for low-entropy values (codes, phones): a plain hash of them is brute-forced
    in seconds, while without the pepper a leaked hash is useless."""
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def generate_otp_code() -> str:
    return f"{secrets.randbelow(10**OTP_LENGTH):0{OTP_LENGTH}d}"


def hash_otp_code(phone: str, code: str, pepper: str) -> str:
    # The phone is part of the hash, so a code hash copied to another phone's row does not match.
    return keyed_hash(f"{phone}:{code}", pepper)


class OtpOutcome(StrEnum):
    ACCEPTED = "accepted"
    WRONG = "wrong"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OtpCheck:
    outcome: OtpOutcome
    attempts: int


@dataclass(frozen=True)
class OtpChallenge:
    code_hash: str
    expires_at: datetime
    attempts: int

    def check(self, code_hash: str, now: datetime) -> OtpCheck:
        """Counts the attempt; the caller stores `attempts` back before reporting a wrong code."""
        if now >= self.expires_at or self.attempts >= OTP_MAX_ATTEMPTS:
            return OtpCheck(OtpOutcome.EXPIRED, self.attempts)
        attempts = self.attempts + 1
        if hmac.compare_digest(self.code_hash, code_hash):
            return OtpCheck(OtpOutcome.ACCEPTED, attempts)
        # The last wrong attempt already says "expired", so the client asks for a new code
        # instead of letting the user type a sixth one that would be rejected anyway.
        outcome = OtpOutcome.EXPIRED if attempts >= OTP_MAX_ATTEMPTS else OtpOutcome.WRONG
        return OtpCheck(outcome, attempts)


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    # 256 random bits cannot be brute-forced, so a plain hash is enough here, unlike codes.
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class RefreshTokenState:
    used: bool
    session_revoked: bool
    session_expires_at: datetime


class RefreshDecision(StrEnum):
    ROTATE = "rotate"
    REUSE_DETECTED = "reuse-detected"
    REJECT = "reject"


def decide_refresh(state: RefreshTokenState | None, now: datetime) -> RefreshDecision:
    if state is None:
        return RefreshDecision.REJECT
    # A used token comes back only if it was copied: either the thief or the owner is replaying
    # it, and we cannot tell which, so every session of the device goes (docs/03-security.md).
    if state.used:
        return RefreshDecision.REUSE_DETECTED
    if state.session_revoked or now >= state.session_expires_at:
        return RefreshDecision.REJECT
    return RefreshDecision.ROTATE


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID


def issue_access_token(claims: AccessClaims, secret: str, now: datetime) -> str:
    payload = {
        "iss": JWT_ISSUER,
        "aud": CLIENT_AUDIENCE,
        "sub": str(claims.user_id),
        "sid": str(claims.session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def read_access_token(token: str, secret: str) -> AccessClaims:
    try:
        # The algorithm list is fixed: the token header must never choose it ("none", RS/HS swap).
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            audience=CLIENT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["iss", "aud", "sub", "sid", "iat", "exp"]},
        )
        return AccessClaims(user_id=UUID(payload["sub"]), session_id=UUID(payload["sid"]))
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise AccessTokenInvalidError from exc
