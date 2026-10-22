from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.identity.domain import OtpChallenge, RefreshTokenState
from flagman.identity.models import AuthSession, PendingOtp, RefreshToken, User


async def save_pending_otp(
    db: AsyncSession, phone: str, code_hash: str, expires_at: datetime, now: datetime
) -> None:
    values = {"code_hash": code_hash, "attempts": 0, "expires_at": expires_at, "created_at": now}
    statement = insert(PendingOtp).values(phone=phone, **values)
    await db.execute(
        statement.on_conflict_do_update(index_elements=[PendingOtp.phone], set_=values)
    )


async def get_pending_otp_for_update(db: AsyncSession, phone: str) -> OtpChallenge | None:
    # The row lock makes concurrent guesses for one phone take turns, so none skips the count.
    row = await db.scalar(select(PendingOtp).where(PendingOtp.phone == phone).with_for_update())
    if row is None:
        return None
    return OtpChallenge(code_hash=row.code_hash, expires_at=row.expires_at, attempts=row.attempts)


async def set_otp_attempts(db: AsyncSession, phone: str, attempts: int) -> None:
    await db.execute(update(PendingOtp).where(PendingOtp.phone == phone).values(attempts=attempts))


async def delete_pending_otp(db: AsyncSession, phone: str) -> None:
    await db.execute(delete(PendingOtp).where(PendingOtp.phone == phone))


async def get_or_create_user(db: AsyncSession, phone: str, now: datetime) -> UUID:
    # ON CONFLICT instead of select-then-insert: two first sign-ins at once must not both insert.
    created: UUID | None = await db.scalar(
        insert(User)
        .values(phone=phone, created_at=now)
        .on_conflict_do_nothing(index_elements=[User.phone])
        .returning(User.id)
    )
    if created is not None:
        return created
    existing = await db.scalar(select(User.id).where(User.phone == phone))
    if existing is None:
        raise RuntimeError("user vanished between insert and select")
    return existing


async def create_session(
    db: AsyncSession, user_id: UUID, device_id: UUID, expires_at: datetime, now: datetime
) -> UUID:
    session = AuthSession(
        user_id=user_id, device_id=device_id, created_at=now, expires_at=expires_at
    )
    db.add(session)
    await db.flush()
    return session.id


async def extend_session(db: AsyncSession, session_id: UUID, expires_at: datetime) -> None:
    await db.execute(
        update(AuthSession).where(AuthSession.id == session_id).values(expires_at=expires_at)
    )


async def revoke_session(db: AsyncSession, session_id: UUID, now: datetime) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_device_sessions(
    db: AsyncSession, user_id: UUID, device_id: UUID, now: datetime
) -> None:
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.device_id == device_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def add_refresh_token(
    db: AsyncSession, session_id: UUID, token_hash: str, now: datetime
) -> None:
    db.add(RefreshToken(token_hash=token_hash, session_id=session_id, created_at=now))
    await db.flush()


@dataclass(frozen=True)
class RefreshTokenRecord:
    session_id: UUID
    user_id: UUID
    device_id: UUID
    state: RefreshTokenState


async def get_refresh_token_for_update(
    db: AsyncSession, token_hash: str
) -> RefreshTokenRecord | None:
    # Both rows are locked: two refreshes with the same token take turns, and the second one
    # sees the token as used, i.e. as reuse, instead of both rotating it.
    row = (
        await db.execute(
            select(RefreshToken, AuthSession)
            .join(AuthSession, RefreshToken.session_id == AuthSession.id)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return None
    token, session = row
    return RefreshTokenRecord(
        session_id=session.id,
        user_id=session.user_id,
        device_id=session.device_id,
        state=RefreshTokenState(
            used=token.used_at is not None,
            session_revoked=session.revoked_at is not None,
            session_expires_at=session.expires_at,
        ),
    )


async def mark_refresh_token_used(db: AsyncSession, token_hash: str, now: datetime) -> None:
    await db.execute(
        update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(used_at=now)
    )
