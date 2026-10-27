import asyncio
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
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
    AuditTargetType,
    RequestOrigin,
)
from flagman.core.config import Settings
from flagman.core.errors import RateLimitedError
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.core.security import keyed_hash
from flagman.identity import repository
from flagman.identity.domain import (
    ACCESS_TOKEN_TTL,
    AUTH_CLEANUP_BATCH_SIZE,
    OTP_TTL,
    REFRESH_TOKEN_TTL,
    STAFF_ACCESS_TOKEN_TTL,
    STAFF_CHALLENGE_TTL,
    STAFF_SESSION_TTL,
    AccessClaims,
    AccessTokenInvalidError,
    Language,
    OtpExpiredError,
    OtpInvalidError,
    OtpOutcome,
    RefreshDecision,
    RefreshTokenInvalidError,
    StaffAccess,
    StaffAccessClaims,
    StaffAlreadyDisabledError,
    StaffChallengeInvalidError,
    StaffCredentialsInvalidError,
    StaffEmailTakenError,
    StaffNextStep,
    StaffNotFoundError,
    StaffRole,
    StaffRoleForbiddenError,
    TotpAlreadyEnrolledError,
    TotpEnrollmentNotStartedError,
    TotpInvalidError,
    TotpResetRequiredError,
    check_birth_date,
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
    normalize_totp_secret,
    password_needs_rehash,
    read_staff_access,
    read_staff_challenge,
    totp_provisioning_uri,
    verify_password,
)
from flagman.identity.ports import SmsSender
from flagman.identity.repository import ClientProfileRecord, StaffRecord
from flagman.identity.schemas import (
    ClientProfile,
    ClientProfileUpdate,
    OtpRequestAccepted,
    StaffAccessToken,
    StaffChallengeIssued,
    StaffMemberList,
    StaffMemberSummary,
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
class AuthCleanupResult:
    pending_otps: int
    client_sessions: int
    staff_sessions: int


async def clean_up_auth_data(
    db: AsyncSession,
    now: datetime,
    retention: timedelta,
    batch_size: int = AUTH_CLEANUP_BATCH_SIZE,
) -> AuthCleanupResult:
    """Deletes expired codes, and sessions that ended more than `retention` ago with their tokens.

    Each batch commits on its own, so the locks stay short. Until a revoked session is deleted, its
    old used token still revokes the sessions of its device when replayed (docs/adr/0009).
    """
    ended_before = now - retention
    return AuthCleanupResult(
        pending_otps=await _delete_in_batches(
            db,
            "pending_otps",
            lambda: repository.delete_expired_otps(db, now, batch_size),
            batch_size,
        ),
        client_sessions=await _delete_in_batches(
            db,
            "client_sessions",
            lambda: repository.delete_ended_sessions(db, ended_before, batch_size),
            batch_size,
        ),
        staff_sessions=await _delete_in_batches(
            db,
            "staff_sessions",
            lambda: repository.delete_ended_staff_sessions(db, ended_before, batch_size),
            batch_size,
        ),
    )


async def _delete_in_batches(
    db: AsyncSession, kind: str, delete_batch: Callable[[], Awaitable[int]], batch_size: int
) -> int:
    total = 0
    while True:
        try:
            async with db.begin():
                await repository.limit_lock_wait(db)
                deleted = await delete_batch()
        except repository.CleanupLockTimeoutError:
            # A row stayed locked by a sign-in or a refresh: the next run takes it, and the other
            # kinds are still cleaned up now.
            logger.warning("auth_cleanup_lock_timeout", kind=kind, deleted=total)
            return total
        total += deleted
        if deleted < batch_size:
            return total


@dataclass(frozen=True)
class ClientDevice:
    device_id: UUID
    # Only a first sign-in uses it, as the language the new client starts with.
    language: Language


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
        self, phone: str, code: str, device: ClientDevice, client_ip: str, now: datetime
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
                    tokens = await self._sign_in(phone, device, now)
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
            elif record is not None and decision is RefreshDecision.REPLAYED_AFTER_END:
                logger.warning(
                    "refresh_token_replayed_after_session_end",
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

    async def _sign_in(self, phone: str, device: ClientDevice, now: datetime) -> TokenPair:
        user_id = await repository.get_or_create_user(self.db, phone, device.language, now)
        # A new sign-in on the same device replaces whatever session it had before.
        await repository.revoke_device_sessions(self.db, user_id, device.device_id, now)
        session_id = await repository.create_session(
            self.db, user_id, device.device_id, now + REFRESH_TOKEN_TTL, now
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


def _client_profile(record: ClientProfileRecord) -> ClientProfile:
    return ClientProfile(
        first_name=record.first_name,
        last_name=record.last_name,
        birth_date=record.birth_date,
        email=record.email,
        language=record.language,
        phone=record.phone,
    )


# The operator checks the documents against these, so they must not change under review (#35).
_CHECKED_BY_KYC = frozenset({"first_name", "last_name", "birth_date"})


class PersonalDataLock(Protocol):
    """Refuses a change of the fields KYC checks, or lets it through and holds that answer.

    Implemented by `kyc.service.lock_personal_data` and handed in by the router: kyc already
    depends on identity, so identity cannot import kyc.
    """

    async def __call__(self, db: AsyncSession, user_id: UUID) -> None: ...


@dataclass(frozen=True)
class ClientProfileService:
    """The signed-in client's own profile: the user id always comes from the access token."""

    db: AsyncSession
    personal_data_lock: PersonalDataLock

    async def get_profile(self, user_id: UUID) -> ClientProfile:
        async with self.db.begin():
            record = await repository.get_client_profile(self.db, user_id)
        if record is None:
            raise AccessTokenInvalidError
        return _client_profile(record)

    async def update_profile(
        self, user_id: UUID, update: ClientProfileUpdate, now: datetime
    ) -> ClientProfile:
        if update.birth_date is not None:
            # The UTC date rather than Minsk's: for three hours a day a birth date of "today" is
            # turned down, which no adult client ever sends.
            check_birth_date(update.birth_date, now.date())
        changes = update.changes()
        async with self.db.begin():
            checked = _CHECKED_BY_KYC & changes.keys()
            if checked:
                # Locked: a concurrent change cannot slip in between the comparison and the write.
                current = await repository.get_client_profile(self.db, user_id, for_update=True)
                if current is None:
                    raise AccessTokenInvalidError
                # The app sends the whole form back: a field sent unchanged is not a change.
                if any(getattr(current, name) != changes[name] for name in checked):
                    await self.personal_data_lock(self.db, user_id)
            record = await repository.update_client_profile(self.db, user_id, changes)
        if record is None:
            raise AccessTokenInvalidError
        return _client_profile(record)


@dataclass(frozen=True)
class ClientIdentity:
    """What the KYC operator checks the documents against; the rest of the profile stays here."""

    first_name: str | None
    last_name: str | None
    birth_date: date | None


async def get_client_identities(
    db: AsyncSession, user_ids: Collection[UUID]
) -> dict[UUID, ClientIdentity]:
    """For the KYC review (#35); runs in the caller's transaction. Unknown ids are left out."""
    records = await repository.get_client_profiles(db, user_ids)
    return {
        user_id: ClientIdentity(record.first_name, record.last_name, record.birth_date)
        for user_id, record in records.items()
    }


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
        challenge_used = False
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
            elif not await self._use_challenge_once(challenge.challenge_id):
                # A valid code with a spent challenge: someone has the challenge and the codes.
                challenge_used = True
                await audit.record(
                    self.db, _sign_in_failed(actor, origin, "totp", "challenge_used")
                )
            else:
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
        if challenge_used:
            raise StaffChallengeInvalidError
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
            elif record is not None and decision is RefreshDecision.REPLAYED_AFTER_END:
                logger.warning(
                    "staff_refresh_token_replayed_after_session_end", staff_id=str(record.staff_id)
                )
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

    async def _use_challenge_once(self, challenge_id: str) -> bool:
        """False if the challenge already opened a session.

        Without this, the same challenge and the next code would open a second session within
        its five minutes. A wrong code does not use it up: the user may retry.
        """
        try:
            await self.limiter.hit(STAFF_CHALLENGE_SESSION_LIMIT, challenge_id)
        except RateLimitedError:
            return False
        return True

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


async def is_staff_session_active(
    db: AsyncSession, staff_id: UUID, session_id: UUID, now: datetime
) -> bool:
    """Whether the staff member is still signed in: for connections that outlive their token.

    False once the session is revoked or over, or the account is disabled.
    """
    async with db.begin():
        return await repository.is_staff_session_active(db, staff_id, session_id, now)


async def authenticate_staff(
    db: AsyncSession, token: str, secret: str, roles: Collection[StaffRole], now: datetime
) -> StaffAccess:
    """The staff member behind an access token, checked in the database too.

    For what outlives a request or must not wait out the token: a revoked session or a disabled
    account is refused at once. Raises AccessTokenInvalidError for a bad or expired token and for
    a session that is over, StaffRoleForbiddenError for a role not in `roles`.
    """
    access = read_staff_access(token, secret)
    if access.claims.role not in roles:
        raise StaffRoleForbiddenError
    if not await is_staff_session_active(db, access.claims.staff_id, access.claims.session_id, now):
        raise AccessTokenInvalidError
    return access


async def list_staff(db: AsyncSession) -> StaffMemberList:
    """Who is who in the admin panel's audit log (#56), disabled staff included.

    Staff are added by hand with `flagman-staff`, so the list stays short enough to go in one
    response.
    """
    async with db.begin():
        staff = await repository.list_staff(db)
    return StaffMemberList(
        items=[
            StaffMemberSummary(id=item.id, email=item.email, disabled=item.disabled)
            for item in staff
        ]
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
                target=AuditTarget(AuditTargetType.STAFF_MEMBER, staff_id),
                details={"role": role.value},
            ),
        )
    return staff_id


@dataclass(frozen=True)
class StandStaff:
    email: str
    password: str
    role: StaffRole
    totp_secret: str


@dataclass(frozen=True)
class EnsuredStaff:
    id: UUID
    role: StaffRole
    created: bool


def check_stand_staff(account: StandStaff) -> str:
    """The normalized 2FA secret; raises PasswordPolicyError, TotpSecretPolicyError."""
    check_password_policy(account.password)
    return normalize_totp_secret(account.totp_secret)


async def ensure_staff(
    db: AsyncSession, account: StandStaff, totp_key: bytes, now: datetime
) -> EnsuredStaff:
    """For the stand seed (#123): a staff member with 2FA already set up, safe to run again.

    An existing account is left as it is, even with another role or password: changing them
    would end the sessions of whoever is showing the stand. `set-password` and `reset-totp` do
    that on purpose. Raises PasswordPolicyError, TotpSecretPolicyError, both checked first so a
    broken seed fails on every run, not only on the first one.
    """
    secret = check_stand_staff(account)
    email = normalize_email(account.email)
    role = account.role
    async with db.begin():
        existing = await repository.get_staff_by_email(db, email)
    if existing is not None:
        return EnsuredStaff(existing.id, existing.role, created=False)
    password_hash = await asyncio.to_thread(hash_password, account.password)
    async with db.begin():
        staff_id = await repository.create_staff(db, email, password_hash, role, now)
        if staff_id is None:
            # Another seed run created it in the meantime: the goal is reached either way.
            raced = await _get_staff(db, email)
            return EnsuredStaff(raced.id, raced.role, created=False)
        ciphertext = encrypt_totp_secret(secret, totp_key, staff_id)
        await repository.set_enrolled_totp_secret(db, staff_id, ciphertext, now)
        await audit.record(
            db,
            AuditEntry(
                SYSTEM,
                AuditAction.STAFF_CREATED,
                RequestOrigin(),
                target=AuditTarget(AuditTargetType.STAFF_MEMBER, staff_id),
                details={"role": role.value, "totp_preset": True},
            ),
        )
    return EnsuredStaff(staff_id, role, created=True)


async def reset_staff_totp(db: AsyncSession, email: str, now: datetime) -> UUID:
    """Lost phone or a new encryption key: 2FA is set up again at the next sign-in.

    Every session of the staff member ends too: the old authenticator may be in wrong hands.
    Until the next sign-in the password alone is enough to set up 2FA, so on a suspected
    break-in the password is changed as well (`set_staff_password`).
    """
    async with db.begin():
        staff = await _get_staff(db, email)
        await _end_access(db, staff.id, AuditAction.STAFF_TOTP_RESET, now)
        await repository.clear_staff_totp(db, staff.id)
    return staff.id


async def set_staff_password(db: AsyncSession, email: str, password: str, now: datetime) -> UUID:
    """Forgotten or leaked password; raises PasswordPolicyError, StaffNotFoundError."""
    check_password_policy(password)
    password_hash = await asyncio.to_thread(hash_password, password)
    async with db.begin():
        staff = await _get_staff(db, email)
        await _end_access(db, staff.id, AuditAction.STAFF_PASSWORD_CHANGED, now)
        await repository.set_staff_password_hash(db, staff.id, password_hash)
    return staff.id


async def disable_staff(db: AsyncSession, email: str, now: datetime) -> UUID:
    """The staff member left or the account is compromised: no sign-in and no refresh after this.

    Raises StaffAlreadyDisabledError for a disabled account: its sessions ended when it was
    disabled, and a second event would only clutter the audit log. There is no command to
    enable an account again yet: that comes with staff management in the admin panel.
    """
    async with db.begin():
        staff = await _get_staff(db, email)
        if staff.disabled:
            raise StaffAlreadyDisabledError
        await _end_access(db, staff.id, AuditAction.STAFF_DISABLED, now)
        await repository.disable_staff(db, staff.id, now)
    return staff.id


async def _get_staff(db: AsyncSession, email: str) -> StaffRecord:
    staff = await repository.get_staff_by_email(db, normalize_email(email))
    if staff is None:
        raise StaffNotFoundError
    return staff


async def _end_access(db: AsyncSession, staff_id: UUID, action: AuditAction, now: datetime) -> None:
    """Shared by the commands that change how a staff member signs in: every session ends.

    Access tokens are not checked against the database, so they keep working for up to
    STAFF_ACCESS_TOKEN_TTL (docs/adr/0011).
    """
    await repository.revoke_all_staff_sessions(db, staff_id, now)
    await audit.record(
        db,
        AuditEntry(
            SYSTEM,
            action,
            RequestOrigin(),
            target=AuditTarget(AuditTargetType.STAFF_MEMBER, staff_id),
        ),
    )
