"""Sign-in rules: one-time codes, passwords, TOTP, access tokens and refresh token rotation.

No I/O here.
"""

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cache
from http import HTTPStatus
from typing import Any
from uuid import UUID

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from flagman.core.errors import AppError
from flagman.core.security import keyed_hash

OTP_LENGTH = 6
OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5

ACCESS_TOKEN_TTL = timedelta(minutes=15)
# Sliding: every rotation moves the end of the session, so an app in use never signs out.
REFRESH_TOKEN_TTL = timedelta(days=30)
# Two browser tabs share one refresh cookie, and a reload can resend it before the new one
# arrives. A token that was rotated this recently is turned down without revoking anything.
REFRESH_REUSE_GRACE = timedelta(seconds=30)

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "flagman"
# Each kind of token has its own audience, so a token of one kind is never accepted as another:
# a client token does not open the admin panel, and a half-done staff sign-in is not a session.
CLIENT_AUDIENCE = "client"
STAFF_AUDIENCE = "staff"
STAFF_CHALLENGE_AUDIENCE = "staff-challenge"


class OtpInvalidError(AppError):
    status = HTTPStatus.BAD_REQUEST
    slug = "otp-invalid"
    title = "Invalid code"


class OtpExpiredError(AppError):
    """The code expired, ran out of attempts or was never requested: the client asks for a new one.

    These cases are not told apart from each other. A wrong guess while a code is pending gets
    `otp-invalid` instead, so a caller can learn that a code was requested for the phone in the
    last minutes; anyone can request one anyway, so this reveals little.
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


class StaffRole(StrEnum):
    OPERATOR = "operator"
    TECHNICIAN = "technician"
    ADMIN = "admin"


class StaffRoleForbiddenError(AppError):
    """A signed-in staff member opened a section of another role."""

    status = HTTPStatus.FORBIDDEN
    slug = "forbidden"
    title = "Forbidden"


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
    used_at: datetime | None
    session_revoked: bool
    session_expires_at: datetime


class RefreshDecision(StrEnum):
    ROTATE = "rotate"
    REUSE_DETECTED = "reuse-detected"
    # Rotated moments ago by a concurrent request of the same client: the client retries with
    # the newer token, so the session is left alone.
    ALREADY_ROTATED = "already-rotated"
    REJECT = "reject"


def decide_refresh(state: RefreshTokenState | None, now: datetime) -> RefreshDecision:
    if state is None:
        return RefreshDecision.REJECT
    if state.used_at is not None:
        if not state.session_revoked and now < state.used_at + REFRESH_REUSE_GRACE:
            return RefreshDecision.ALREADY_ROTATED
        # Later on, a used token comes back only if it was copied: either the thief or the owner
        # is replaying it, and we cannot tell which, so every session of the device goes
        # (docs/03-security.md).
        return RefreshDecision.REUSE_DETECTED
    if state.session_revoked or now >= state.session_expires_at:
        return RefreshDecision.REJECT
    return RefreshDecision.ROTATE


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID


def issue_access_token(claims: AccessClaims, secret: str, now: datetime) -> str:
    return _encode_jwt(
        CLIENT_AUDIENCE, claims.user_id, ACCESS_TOKEN_TTL, now, secret, sid=str(claims.session_id)
    )


def read_access_token(token: str, secret: str) -> AccessClaims:
    payload = _decode_jwt(token, secret, CLIENT_AUDIENCE, ["sid"], AccessTokenInvalidError)
    try:
        return AccessClaims(user_id=UUID(payload["sub"]), session_id=UUID(payload["sid"]))
    except (ValueError, TypeError) as exc:
        raise AccessTokenInvalidError from exc


def _encode_jwt(
    audience: str, subject: UUID, ttl: timedelta, now: datetime, secret: str, **claims: str
) -> str:
    payload = {
        "iss": JWT_ISSUER,
        "aud": audience,
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        **claims,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def _decode_jwt(
    token: str, secret: str, audience: str, claims: list[str], error: type[AppError]
) -> dict[str, Any]:
    try:
        # The algorithm list is fixed: the token header must never choose it ("none", RS/HS swap).
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            audience=audience,
            issuer=JWT_ISSUER,
            options={"require": ["iss", "aud", "sub", "iat", "exp", *claims]},
        )
    except jwt.InvalidTokenError as exc:
        raise error from exc
    return payload


# Staff sign-in (#32, docs/adr/0011): email and password, then a TOTP code.

STAFF_ACCESS_TOKEN_TTL = ACCESS_TOKEN_TTL
# Counted from the sign-in and never extended, unlike the client's sliding month: the admin panel
# is the most valuable account to steal, so even a tab left open all night signs in again daily.
STAFF_SESSION_TTL = timedelta(hours=12)
# Between the password step and the TOTP step; enough to open the authenticator app.
STAFF_CHALLENGE_TTL = timedelta(minutes=5)

EMAIL_MAX_LENGTH = 254  # RFC 5321 path limit
# Only rejects obvious garbage; shared by the API schema and the command that creates staff,
# so the command cannot create an account nobody can sign in to.
EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+$"
# The upper bound keeps argon2 from hashing megabytes sent by a caller.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

TOTP_DIGITS = 6
TOTP_INTERVAL_SECONDS = 30
# One step either side: phones and servers disagree on the time by a few seconds.
TOTP_VALID_WINDOW = 1
TOTP_ISSUER = "FLAGMAN Drive"
_TOTP_NONCE_BYTES = 12

# RFC 9106 low-memory profile (argon2id, 64 MiB, 3 passes): the library default, chosen so that
# hashing stays well under a second on a small VPS.
_password_hasher = PasswordHasher()


class StaffCredentialsInvalidError(AppError):
    """Wrong password, unknown email or disabled account: not told apart from each other."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "staff-credentials-invalid"
    title = "Invalid email or password"


class StaffChallengeInvalidError(AppError):
    """The password step expired, belongs to another step or was tampered with: start again."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "staff-challenge-invalid"
    title = "Sign in again"


class TotpInvalidError(AppError):
    """Wrong, reused or out-of-window code: the user may retry with the same challenge."""

    status = HTTPStatus.BAD_REQUEST
    slug = "totp-invalid"
    title = "Invalid code"


class TotpEnrollmentNotStartedError(AppError):
    """A code came for 2FA setup before the QR code was requested."""

    status = HTTPStatus.CONFLICT
    slug = "totp-enrollment-not-started"
    title = "Set up two-factor authentication first"


class TotpAlreadyEnrolledError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "totp-already-enrolled"
    title = "Two-factor authentication is already set up"


class TotpResetRequiredError(AppError):
    """The stored secret does not decrypt, e.g. after API_TOTP_ENCRYPTION_KEY was changed."""

    status = HTTPStatus.CONFLICT
    slug = "totp-reset-required"
    title = "Ask an administrator to reset two-factor authentication"


class PasswordPolicyError(ValueError):
    pass


class StaffEmailTakenError(ValueError):
    pass


class StaffNotFoundError(LookupError):
    pass


def normalize_email(email: str) -> str:
    # Case-insensitive in practice everywhere, so one person cannot get two accounts.
    return email.strip().lower()


def check_password_policy(password: str) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(
            f"password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters long"
        )
    if not password.strip():
        raise PasswordPolicyError("password must not be blank")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True after the hashing parameters change: the hash is upgraded on the next sign-in."""
    return _password_hasher.check_needs_rehash(password_hash)


@cache
def dummy_password_hash() -> str:
    """Verified against when the email is unknown, so the answer takes as long as for a real one."""
    return _password_hasher.hash(secrets.token_urlsafe(16))


def generate_totp_secret() -> str:
    return pyotp.random_base32()  # 160 bits, as RFC 4226 recommends


def totp_provisioning_uri(secret: str, email: str) -> str:
    """`otpauth://` URI for the QR code; authenticator apps show the issuer and the email."""
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS).provisioning_uri(
        name=email, issuer_name=TOTP_ISSUER
    )


def totp_step(now: datetime) -> int:
    return int(now.timestamp()) // TOTP_INTERVAL_SECONDS


def match_totp(secret: str, code: str, now: datetime, last_used_step: int | None) -> int | None:
    """Returns the time step the code belongs to, or None if it does not match.

    A step at or before the last accepted one never matches: a code seen over someone's shoulder
    or replayed from a log cannot be used a second time.
    """
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS)
    current = totp_step(now)
    for step in range(current - TOTP_VALID_WINDOW, current + TOTP_VALID_WINDOW + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def encrypt_totp_secret(secret: str, key: bytes, staff_id: UUID) -> bytes:
    # The staff id is authenticated data: a ciphertext copied to another row does not decrypt.
    nonce = secrets.token_bytes(_TOTP_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, secret.encode(), staff_id.bytes)


def decrypt_totp_secret(ciphertext: bytes, key: bytes, staff_id: UUID) -> str:
    nonce, sealed = ciphertext[:_TOTP_NONCE_BYTES], ciphertext[_TOTP_NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, sealed, staff_id.bytes).decode()


class StaffNextStep(StrEnum):
    """What the admin panel shows after the password: the code field or the QR code first."""

    TOTP = "totp"
    TOTP_ENROLLMENT = "totp_enrollment"


@dataclass(frozen=True)
class StaffChallenge:
    staff_id: UUID
    # Lets the service open at most one session per challenge.
    challenge_id: str


def issue_staff_challenge(staff_id: UUID, secret: str, now: datetime) -> str:
    """Proof that the password step passed; the TOTP step finishes the sign-in.

    It carries no step: the TOTP step reads the current 2FA state from the database, so two tabs
    setting up 2FA at once cannot enroll a secret behind each other's back.
    """
    return _encode_jwt(
        STAFF_CHALLENGE_AUDIENCE,
        staff_id,
        STAFF_CHALLENGE_TTL,
        now,
        secret,
        jti=secrets.token_urlsafe(16),
    )


def read_staff_challenge(token: str, secret: str) -> StaffChallenge:
    payload = _decode_jwt(
        token, secret, STAFF_CHALLENGE_AUDIENCE, ["jti"], StaffChallengeInvalidError
    )
    try:
        return StaffChallenge(staff_id=UUID(payload["sub"]), challenge_id=str(payload["jti"]))
    except (ValueError, TypeError) as exc:
        raise StaffChallengeInvalidError from exc


@dataclass(frozen=True)
class StaffAccessClaims:
    staff_id: UUID
    session_id: UUID
    role: StaffRole


def issue_staff_access_token(claims: StaffAccessClaims, secret: str, now: datetime) -> str:
    # The role travels in the token, so a role change takes effect within STAFF_ACCESS_TOKEN_TTL.
    return _encode_jwt(
        STAFF_AUDIENCE,
        claims.staff_id,
        STAFF_ACCESS_TOKEN_TTL,
        now,
        secret,
        sid=str(claims.session_id),
        role=claims.role.value,
    )


def read_staff_access_token(token: str, secret: str) -> StaffAccessClaims:
    payload = _decode_jwt(token, secret, STAFF_AUDIENCE, ["sid", "role"], AccessTokenInvalidError)
    try:
        return StaffAccessClaims(
            staff_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            role=StaffRole(payload["role"]),
        )
    except (ValueError, TypeError) as exc:
        raise AccessTokenInvalidError from exc
