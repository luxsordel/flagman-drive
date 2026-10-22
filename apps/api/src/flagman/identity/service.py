import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog
from cryptography.exceptions import InvalidTag
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    SYSTEM,
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    RequestOrigin,
)
from flagman.core.config import Settings
from flagman.core.errors import RateLimitedError
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.core.security import keyed_hash
from flagman.identity import repository
from flagman.identity.domain import (
    ACCESS_TOKEN_TTL,
    OTP_TTL,
    REFRESH_TOKEN_TTL,
    STAFF_ACCESS_TOKEN_TTL,
    STAFF_CHALLENGE_TTL,
    STAFF_SESSION_TTL,
    AccessClaims,
    AccessTokenInvalidError,
    OtpExpiredError,
    OtpInvalidError,
    OtpOutcome,
    RefreshDecision,
    RefreshTokenInvalidError,
    StaffAccessClaims,
    StaffChallengeInvalidError,
    StaffCredentialsInvalidError,
    StaffEmailTakenError,
    StaffNextStep,
    StaffNotFoundError,
    StaffRole,
    TotpAlreadyEnrolledError,
    TotpEnrollmentNotStartedError,
    TotpInvalidError,
    TotpResetRequiredError,
    check_password_policy,
    decide_refresh,
    decrypt_totp_secret,
    dummy_password_hash,
    encrypt_totp_secret,
    generate_refresh_token,
    generate_totp_secret,
    hash_otp_code,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    issue_staff_access_token,
    issue_staff_challenge,
    match_totp,
    normalize_email,
    password_needs_rehash,
    read_staff_challenge,
    totp_provisioning_uri,
    verify_password,
)
from flagman.identity.ports import SmsSender
from flagman.identity.schemas import (
    OtpRequestAccepted,
    StaffAccessToken,
    StaffChallengeIssued,
    StaffProfile,
    StaffTotpEnrollment,
    TokenPair,
)

logger = structlog.get_logger(__name__)

OTP_RESEND_AFTER = timedelta(seconds=60)
_HOUR = 3600

# SMS cost money and annoy people, so code requests are limited both per phone and per IP.
OTP_RESEND_LIMIT = Limit("otp-request-phone-cooldown", 1, int(OTP_RESEND_AFTER.total_seconds()))
OTP_PHONE_LIMIT = Limit("otp-request-phone", 5, _HOUR)
OTP_IP_LIMIT = Limit("otp-request-ip", 20, _HOUR)
# Five attempts per code times five codes per hour would allow 25 guesses; this caps it at 10.
OTP_VERIFY_LIMIT = Limit("otp-verify-phone", 10, _HOUR)
# Against guessing spread over many phones. Higher than the request limit: a code may take
# several tries, and mobile carriers put many clients behind one address.
OTP_VERIFY_IP_LIMIT = Limit("otp-verify-ip", 50, _HOUR)

# Against password guessing for one account and across many. Every attempt counts, so anyone who
# knows a staff email can lock that account for an hour; acceptable for a handful of staff
# (docs/adr/0011).
STAFF_PASSWORD_EMAIL_LIMIT = Limit("staff-password-email", 10, _HOUR)
STAFF_PASSWORD_IP_LIMIT = Limit("staff-password-ip", 30, _HOUR)
# A challenge stays valid for 5 minutes and may be retried: this caps TOTP guessing per account.
STAFF_TOTP_LIMIT = Limit("staff-totp-staff", 10, _HOUR)
STAFF_TOTP_IP_LIMIT = Limit("staff-totp-ip", 30, _HOUR)
# A challenge opens at most one session; the window outlives the challenge itself.
STAFF_CHALLENGE_SESSION_LIMIT = Limit(
    "staff-challenge-session", 1, int(STAFF_CHALLENGE_TTL.total_seconds())
)

_UNKNOWN_IP = "unknown"


@dataclass(frozen=True)
class SignInService:
    db: AsyncSession
    limiter: RateLimiter
    settings: Settings

    @property
    def _pepper(self) -> str:
        return self.settings.api_otp_pepper.get_secret_value()

    async def request_otp(
        self, sms: SmsSender, phone: str, client_ip: str, now: datetime
    ) -> OtpRequestAccepted:
        phone_key = keyed_hash(phone, self._pepper)
        await self.limiter.hit(OTP_IP_LIMIT, keyed_hash(client_ip, self._pepper))
        await self.limiter.hit(OTP_RESEND_LIMIT, phone_key)
        await self.limiter.hit(OTP_PHONE_LIMIT, phone_key)

        # After the limits: probing which phones may sign in is counted like any other request.
        code = sms.choose_code(phone)
        code_hash = hash_otp_code(phone, code, self._pepper)
        async with self.db.begin():
            await repository.save_pending_otp(self.db, phone, code_hash, now + OTP_TTL, now)
        await sms.send_otp(phone, code)
        return OtpRequestAccepted(
            expires_in=int(OTP_TTL.total_seconds()),
            resend_after=int(OTP_RESEND_AFTER.total_seconds()),
        )

    async def verify_otp(
        self, phone: str, code: str, device_id: UUID, client_ip: str, now: datetime
    ) -> TokenPair:
        await self.limiter.hit(OTP_VERIFY_IP_LIMIT, keyed_hash(client_ip, self._pepper))

        tokens: TokenPair | None = None
        async with self.db.begin():
            challenge = await repository.get_pending_otp_for_update(self.db, phone)
            if challenge is None:
                outcome = OtpOutcome.EXPIRED
            else:
                # Counted only while a code is pending: otherwise anyone could lock a stranger
                # out of signing in for an hour without even triggering an SMS to them.
                await self.limiter.hit(OTP_VERIFY_LIMIT, keyed_hash(phone, self._pepper))
                check = challenge.check(hash_otp_code(phone, code, self._pepper), now)
                outcome = check.outcome
                if outcome is OtpOutcome.WRONG:
                    await repository.set_otp_attempts(self.db, phone, check.attempts)
                else:
                    # Accepted or used up: either way this code must never work again.
                    await repository.delete_pending_otp(self.db, phone)
                if outcome is OtpOutcome.ACCEPTED:
                    tokens = await self._sign_in(phone, device_id, now)
        # Raised after the transaction so that the counted attempt is committed, not rolled back.
        if outcome is OtpOutcome.WRONG:
            raise OtpInvalidError
        if tokens is None:
            raise OtpExpiredError
        return tokens

    async def refresh_tokens(self, refresh_token: str, now: datetime) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        tokens: TokenPair | None = None
        async with self.db.begin():
            record = await repository.get_refresh_token_for_update(self.db, token_hash)
            decision = decide_refresh(record.state if record else None, now)
            if record is not None and decision is RefreshDecision.ROTATE:
                await repository.mark_refresh_token_used(self.db, token_hash, now)
                await repository.extend_session(self.db, record.session_id, now + REFRESH_TOKEN_TTL)
                tokens = await self._issue_tokens(record.user_id, record.session_id, now)
            elif record is not None and decision is RefreshDecision.REUSE_DETECTED:
                await repository.revoke_device_sessions(
                    self.db, record.user_id, record.device_id, now
                )
                # Ids only: enough to investigate, nothing personal.
                logger.warning(
                    "refresh_token_reuse_detected",
                    user_id=str(record.user_id),
                    device_id=str(record.device_id),
                )
            elif record is not None and decision is RefreshDecision.ALREADY_ROTATED:
                # Expected from web tabs; a spike for one device may still be worth a look.
                logger.info(
                    "refresh_token_already_rotated",
                    user_id=str(record.user_id),
                    device_id=str(record.device_id),
                )
        if tokens is None:
            raise RefreshTokenInvalidError
        return tokens

    async def logout(self, refresh_token: str, now: datetime) -> None:
        async with self.db.begin():
            record = await repository.get_refresh_token_for_update(
                self.db, hash_refresh_token(refresh_token)
            )
            if record is not None:
                await repository.revoke_session(self.db, record.session_id, now)

    async def _sign_in(self, phone: str, device_id: UUID, now: datetime) -> TokenPair:
        user_id = await repository.get_or_create_user(self.db, phone, now)
        # A new sign-in on the same device replaces whatever session it had before.
        await repository.revoke_device_sessions(self.db, user_id, device_id, now)
        session_id = await repository.create_session(
            self.db, user_id, device_id, now + REFRESH_TOKEN_TTL, now
        )
        return await self._issue_tokens(user_id, session_id, now)

    async def _issue_tokens(self, user_id: UUID, session_id: UUID, now: datetime) -> TokenPair:
        refresh_token = generate_refresh_token()
        await repository.add_refresh_token(
            self.db, session_id, hash_refresh_token(refresh_token), now
        )
        access_token = issue_access_token(
            AccessClaims(user_id=user_id, session_id=session_id),
            self.settings.api_jwt_secret.get_secret_value(),
            now,
        )
        return TokenPair(
            access_token=access_token,
            expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
            refresh_token=refresh_token,
        )


def _verify_password_or_dummy(password_hash: str | None, password: str) -> bool:
    # An unknown email still costs one argon2 run, so timing does not reveal who has an account.
    if password_hash is None:
        verify_password(dummy_password_hash(), password)
        return False
    return verify_password(password_hash, password)


def _staff_actor(staff_id: UUID) -> Actor:
    return Actor(ActorType.STAFF, staff_id)


def _sign_in_failed(actor: Actor, origin: RequestOrigin, step: str, reason: str) -> AuditEntry:
    return AuditEntry(
        actor, AuditAction.STAFF_SIGN_IN_FAILED, origin, details={"step": step, "reason": reason}
    )


@dataclass(frozen=True)
class StaffTokens:
    access_token: StaffAccessToken
    # Always delivered in the HttpOnly cookie: the admin panel is a web app only.
    refresh_token: str
    # The cookie lives exactly as long as the session, which refreshes do not extend.
    session_expires_at: datetime


@dataclass(frozen=True)
class StaffSignInService:
    db: AsyncSession
    limiter: RateLimiter
    settings: Settings

    @property
    def _pepper(self) -> str:
        return self.settings.api_otp_pepper.get_secret_value()

    @property
    def _jwt_secret(self) -> str:
        return self.settings.api_jwt_secret.get_secret_value()

    async def check_password(
        self, email: str, password: str, origin: RequestOrigin, now: datetime
    ) -> StaffChallengeIssued:
        email = normalize_email(email)
        await self.limiter.hit(STAFF_PASSWORD_IP_LIMIT, origin.ip_hash or _UNKNOWN_IP)
        await self.limiter.hit(STAFF_PASSWORD_EMAIL_LIMIT, keyed_hash(email, self._pepper))

        async with self.db.begin():
            staff = await repository.get_staff_by_email(self.db, email)
        # argon2 takes tens of milliseconds of CPU: off the event loop, and outside a transaction
        # so that no database connection is held meanwhile.
        password_hash = staff.password_hash if staff is not None else None
        matches = await asyncio.to_thread(_verify_password_or_dummy, password_hash, password)
        accepted = staff is not None and matches and not staff.disabled
        new_hash = None
        if accepted and staff is not None and password_needs_rehash(staff.password_hash):
            new_hash = await asyncio.to_thread(hash_password, password)

        async with self.db.begin():
            if staff is None:
                # No staff id to put in the event; the IP hash still shows who keeps trying.
                await audit.record(
                    self.db, _sign_in_failed(SYSTEM, origin, "password", "unknown_email")
                )
            elif not matches or staff.disabled:
                reason = "disabled" if matches else "wrong_password"
                await audit.record(
                    self.db, _sign_in_failed(_staff_actor(staff.id), origin, "password", reason)
                )
            elif new_hash is not None:
                await repository.set_staff_password_hash(self.db, staff.id, new_hash)
        # Raised after the transaction so that the failed attempt is committed, not rolled back.
        if staff is None or not accepted:
            raise StaffCredentialsInvalidError
        return StaffChallengeIssued(
            challenge_token=issue_staff_challenge(staff.id, self._jwt_secret, now),
            expires_in=int(STAFF_CHALLENGE_TTL.total_seconds()),
            next_step=StaffNextStep.TOTP if staff.totp_enrolled else StaffNextStep.TOTP_ENROLLMENT,
        )

    async def start_totp_enrollment(
        self, challenge_token: str, origin: RequestOrigin
    ) -> StaffTotpEnrollment:
        staff_id = read_staff_challenge(challenge_token, self._jwt_secret).staff_id
        # Shares the TOTP limit: otherwise someone with the password could keep replacing the
        # secret while the owner is scanning the QR code.
        await self.limiter.hit(STAFF_TOTP_LIMIT, str(staff_id))
        async with self.db.begin():
            staff = await repository.get_staff(self.db, staff_id, for_update=True)
            if staff is None or staff.disabled:
                raise StaffChallengeInvalidError
            if staff.totp_enrolled:
                # Only an administrator resets 2FA: otherwise a stolen password alone would be
                # enough to replace the owner's authenticator with the thief's.
                raise TotpAlreadyEnrolledError
            secret = generate_totp_secret()
            ciphertext = encrypt_totp_secret(secret, self.settings.totp_encryption_key, staff.id)
            await repository.set_pending_totp_secret(self.db, staff.id, ciphertext)
            await audit.record(
                self.db,
                AuditEntry(
                    _staff_actor(staff.id), AuditAction.STAFF_TOTP_ENROLLMENT_STARTED, origin
                ),
            )
        return StaffTotpEnrollment(
            otpauth_uri=totp_provisioning_uri(secret, staff.email), secret=secret
        )

    async def verify_totp(
        self, challenge_token: str, code: str, origin: RequestOrigin, now: datetime
    ) -> StaffTokens:
        challenge = read_staff_challenge(challenge_token, self._jwt_secret)
        staff_id = challenge.staff_id
        await self.limiter.hit(STAFF_TOTP_IP_LIMIT, origin.ip_hash or _UNKNOWN_IP)
        await self.limiter.hit(STAFF_TOTP_LIMIT, str(staff_id))

        tokens: StaffTokens | None = None
        async with self.db.begin():
            staff = await repository.get_staff(self.db, staff_id, for_update=True)
            if staff is None or staff.disabled:
                raise StaffChallengeInvalidError
            if staff.totp_secret_encrypted is None:
                raise TotpEnrollmentNotStartedError
            try:
                secret = decrypt_totp_secret(
                    staff.totp_secret_encrypted, self.settings.totp_encryption_key, staff.id
                )
            except InvalidTag as exc:
                logger.error("staff_totp_secret_undecryptable", staff_id=str(staff.id))
                raise TotpResetRequiredError from exc

            step = match_totp(secret, code, now, staff.totp_last_step)
            actor = _staff_actor(staff.id)
            if step is None:
                await audit.record(self.db, _sign_in_failed(actor, origin, "totp", "wrong_code"))
            else:
                await self._use_challenge_once(challenge.challenge_id)
                # The first accepted code is what finishes the 2FA setup.
                enrolled_at = None if staff.totp_enrolled else now
                await repository.accept_totp_step(self.db, staff.id, step, enrolled_at)
                if enrolled_at is not None:
                    await audit.record(
                        self.db, AuditEntry(actor, AuditAction.STAFF_TOTP_ENROLLED, origin)
                    )
                expires_at = now + STAFF_SESSION_TTL
                session_id = await repository.create_staff_session(
                    self.db, staff.id, expires_at, now
                )
                tokens = await self._issue_tokens(staff.id, session_id, staff.role, expires_at, now)
                await audit.record(
                    self.db,
                    AuditEntry(
                        actor,
                        AuditAction.STAFF_SIGNED_IN,
                        origin,
                        details={"session_id": session_id},
                    ),
                )
        # Raised after the transaction so that the failed attempt is committed, not rolled back.
        if tokens is None:
            raise TotpInvalidError
        return tokens

    async def refresh_tokens(self, refresh_token: str, now: datetime) -> StaffTokens:
        token_hash = hash_refresh_token(refresh_token)
        tokens: StaffTokens | None = None
        async with self.db.begin():
            record = await repository.get_staff_refresh_token_for_update(self.db, token_hash)
            decision = decide_refresh(record.state if record else None, now)
            if record is not None and record.staff_disabled:
                await repository.revoke_staff_session(self.db, record.session_id, now)
            elif record is not None and decision is RefreshDecision.ROTATE:
                await repository.mark_staff_refresh_token_used(self.db, token_hash, now)
                # The session keeps its end: STAFF_SESSION_TTL counts from the sign-in.
                tokens = await self._issue_tokens(
                    record.staff_id,
                    record.session_id,
                    record.role,
                    record.state.session_expires_at,
                    now,
                )
            elif record is not None and decision is RefreshDecision.REUSE_DETECTED:
                # Staff have no device ids: the session the token belongs to is what goes.
                await repository.revoke_staff_session(self.db, record.session_id, now)
                logger.warning("staff_refresh_token_reuse_detected", staff_id=str(record.staff_id))
            elif record is not None and decision is RefreshDecision.ALREADY_ROTATED:
                logger.info("staff_refresh_token_already_rotated", staff_id=str(record.staff_id))
        if tokens is None:
            raise RefreshTokenInvalidError
        return tokens

    async def logout(self, refresh_token: str, origin: RequestOrigin, now: datetime) -> None:
        async with self.db.begin():
            record = await repository.get_staff_refresh_token_for_update(
                self.db, hash_refresh_token(refresh_token)
            )
            if record is not None and not record.state.session_revoked:
                await repository.revoke_staff_session(self.db, record.session_id, now)
                await audit.record(
                    self.db,
                    AuditEntry(
                        _staff_actor(record.staff_id),
                        AuditAction.STAFF_SIGNED_OUT,
                        origin,
                        details={"session_id": record.session_id},
                    ),
                )

    async def get_profile(self, staff_id: UUID) -> StaffProfile:
        async with self.db.begin():
            staff = await repository.get_staff(self.db, staff_id)
        if staff is None or staff.disabled:
            raise AccessTokenInvalidError
        return StaffProfile(id=staff.id, email=staff.email, role=staff.role)

    async def _use_challenge_once(self, challenge_id: str) -> None:
        # Without this, the same challenge and the next code would open a second session
        # within its five minutes. A wrong code does not use it up: the user may retry.
        try:
            await self.limiter.hit(STAFF_CHALLENGE_SESSION_LIMIT, challenge_id)
        except RateLimitedError as exc:
            raise StaffChallengeInvalidError from exc

    async def _issue_tokens(
        self,
        staff_id: UUID,
        session_id: UUID,
        role: StaffRole,
        session_expires_at: datetime,
        now: datetime,
    ) -> StaffTokens:
        refresh_token = generate_refresh_token()
        await repository.add_staff_refresh_token(
            self.db, session_id, hash_refresh_token(refresh_token), now
        )
        access_token = issue_staff_access_token(
            StaffAccessClaims(staff_id=staff_id, session_id=session_id, role=role),
            self._jwt_secret,
            now,
        )
        return StaffTokens(
            access_token=StaffAccessToken(
                access_token=access_token,
                expires_in=int(STAFF_ACCESS_TOKEN_TTL.total_seconds()),
            ),
            refresh_token=refresh_token,
            session_expires_at=session_expires_at,
        )


async def create_staff(
    db: AsyncSession, email: str, password: str, role: StaffRole, now: datetime
) -> UUID:
    """For `flagman-staff create`; raises PasswordPolicyError, StaffEmailTakenError."""
    check_password_policy(password)
    password_hash = await asyncio.to_thread(hash_password, password)
    async with db.begin():
        staff_id = await repository.create_staff(
            db, normalize_email(email), password_hash, role, now
        )
        if staff_id is None:
            raise StaffEmailTakenError
        await audit.record(
            db,
            AuditEntry(
                SYSTEM,
                AuditAction.STAFF_CREATED,
                RequestOrigin(),
                target=AuditTarget("staff_member", staff_id),
                details={"role": role.value},
            ),
        )
    return staff_id


async def reset_staff_totp(db: AsyncSession, email: str, now: datetime) -> UUID:
    """Lost phone or a new encryption key: 2FA is set up again at the next sign-in.

    Every session of the staff member ends too: the old authenticator may be in wrong hands.
    Until the next sign-in the password alone is enough to set up 2FA, so on a suspected
    break-in the password is changed as well (`set_staff_password`).
    """
    async with db.begin():
        staff_id = await _end_access(db, email, AuditAction.STAFF_TOTP_RESET, now)
        await repository.clear_staff_totp(db, staff_id)
    return staff_id


async def set_staff_password(db: AsyncSession, email: str, password: str, now: datetime) -> UUID:
    """Forgotten or leaked password; raises PasswordPolicyError, StaffNotFoundError."""
    check_password_policy(password)
    password_hash = await asyncio.to_thread(hash_password, password)
    async with db.begin():
        staff_id = await _end_access(db, email, AuditAction.STAFF_PASSWORD_CHANGED, now)
        await repository.set_staff_password_hash(db, staff_id, password_hash)
    return staff_id


async def disable_staff(db: AsyncSession, email: str, now: datetime) -> UUID:
    """The staff member left or the account is compromised: no sign-in and no refresh after this.

    There is no command to enable an account again yet: that comes with staff management in
    the admin panel.
    """
    async with db.begin():
        staff_id = await _end_access(db, email, AuditAction.STAFF_DISABLED, now)
        await repository.disable_staff(db, staff_id, now)
    return staff_id


async def _end_access(db: AsyncSession, email: str, action: AuditAction, now: datetime) -> UUID:
    """Shared by the commands that change how a staff member signs in: every session ends.

    Access tokens are not checked against the database, so they keep working for up to
    STAFF_ACCESS_TOKEN_TTL (docs/adr/0011).
    """
    staff = await repository.get_staff_by_email(db, normalize_email(email))
    if staff is None:
        raise StaffNotFoundError
    await repository.revoke_all_staff_sessions(db, staff.id, now)
    await audit.record(
        db,
        AuditEntry(SYSTEM, action, RequestOrigin(), target=AuditTarget("staff_member", staff.id)),
    )
    return staff.id
