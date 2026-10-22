from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.identity import repository
from flagman.identity.domain import (
    ACCESS_TOKEN_TTL,
    OTP_TTL,
    REFRESH_TOKEN_TTL,
    AccessClaims,
    OtpExpiredError,
    OtpInvalidError,
    OtpOutcome,
    RefreshDecision,
    RefreshTokenInvalidError,
    decide_refresh,
    generate_otp_code,
    generate_refresh_token,
    hash_otp_code,
    hash_refresh_token,
    issue_access_token,
    keyed_hash,
)
from flagman.identity.ports import SmsSender
from flagman.identity.schemas import OtpRequestAccepted, TokenPair

logger = structlog.get_logger(__name__)

OTP_RESEND_AFTER = timedelta(seconds=60)
_HOUR = 3600

# SMS cost money and annoy people, so code requests are limited both per phone and per IP.
OTP_RESEND_LIMIT = Limit("otp-request-phone-cooldown", 1, int(OTP_RESEND_AFTER.total_seconds()))
OTP_PHONE_LIMIT = Limit("otp-request-phone", 5, _HOUR)
OTP_IP_LIMIT = Limit("otp-request-ip", 20, _HOUR)
# Five attempts per code times five codes per hour would allow 25 guesses; this caps it at 10.
OTP_VERIFY_LIMIT = Limit("otp-verify-phone", 10, _HOUR)


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

        code = generate_otp_code()
        code_hash = hash_otp_code(phone, code, self._pepper)
        async with self.db.begin():
            await repository.save_pending_otp(self.db, phone, code_hash, now + OTP_TTL, now)
        await sms.send_otp(phone, code)
        return OtpRequestAccepted(
            expires_in=int(OTP_TTL.total_seconds()),
            resend_after=int(OTP_RESEND_AFTER.total_seconds()),
        )

    async def verify_otp(self, phone: str, code: str, device_id: UUID, now: datetime) -> TokenPair:
        await self.limiter.hit(OTP_VERIFY_LIMIT, keyed_hash(phone, self._pepper))

        tokens: TokenPair | None = None
        async with self.db.begin():
            challenge = await repository.get_pending_otp_for_update(self.db, phone)
            if challenge is None:
                outcome = OtpOutcome.EXPIRED
            else:
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
