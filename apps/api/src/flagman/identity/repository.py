from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.identity.domain import OtpChallenge, RefreshTokenState, StaffRole
from flagman.identity.models import (
    AuthSession,
    PendingOtp,
    RefreshToken,
    StaffMember,
    StaffRefreshToken,
    StaffSession,
    User,
)


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
    # sees the token as used instead of both rotating it.
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
            used_at=token.used_at,
            session_revoked=session.revoked_at is not None,
            session_expires_at=session.expires_at,
        ),
    )


async def mark_refresh_token_used(db: AsyncSession, token_hash: str, now: datetime) -> None:
    await db.execute(
        update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(used_at=now)
    )


@dataclass(frozen=True)
class StaffRecord:
    id: UUID
    email: str
    password_hash: str
    role: StaffRole
    totp_secret_encrypted: bytes | None
    totp_enrolled: bool
    totp_last_step: int | None
    disabled: bool


def _staff_record(row: StaffMember) -> StaffRecord:
    return StaffRecord(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        role=StaffRole(row.role),
        totp_secret_encrypted=row.totp_secret_encrypted,
        totp_enrolled=row.totp_enrolled_at is not None,
        totp_last_step=row.totp_last_step,
        disabled=row.disabled_at is not None,
    )


async def create_staff(
    db: AsyncSession, email: str, password_hash: str, role: StaffRole, now: datetime
) -> UUID | None:
    """Returns None when the email is taken."""
    created: UUID | None = await db.scalar(
        insert(StaffMember)
        .values(email=email, password_hash=password_hash, role=role.value, created_at=now)
        .on_conflict_do_nothing(index_elements=[StaffMember.email])
        .returning(StaffMember.id)
    )
    return created


async def get_staff_by_email(db: AsyncSession, email: str) -> StaffRecord | None:
    row = await db.scalar(select(StaffMember).where(StaffMember.email == email))
    return _staff_record(row) if row is not None else None


async def get_staff(
    db: AsyncSession, staff_id: UUID, *, for_update: bool = False
) -> StaffRecord | None:
    statement = select(StaffMember).where(StaffMember.id == staff_id)
    # Locked for the TOTP step: two requests with the same code take turns, and the second one
    # sees the step as used instead of both signing in.
    row = await db.scalar(statement.with_for_update() if for_update else statement)
    return _staff_record(row) if row is not None else None


async def set_staff_password_hash(db: AsyncSession, staff_id: UUID, password_hash: str) -> None:
    await db.execute(
        update(StaffMember).where(StaffMember.id == staff_id).values(password_hash=password_hash)
    )


async def set_pending_totp_secret(db: AsyncSession, staff_id: UUID, ciphertext: bytes) -> None:
    # The enrolled-at check makes the update a no-op for staff who finished the setup meanwhile.
    await db.execute(
        update(StaffMember)
        .where(StaffMember.id == staff_id, StaffMember.totp_enrolled_at.is_(None))
        .values(totp_secret_encrypted=ciphertext)
    )


async def accept_totp_step(
    db: AsyncSession, staff_id: UUID, step: int, enrolled_at: datetime | None
) -> None:
    """Stores the used step; `enrolled_at` is set only by the code that finishes the setup."""
    values: dict[str, object] = {"totp_last_step": step}
    if enrolled_at is not None:
        values["totp_enrolled_at"] = enrolled_at
    await db.execute(update(StaffMember).where(StaffMember.id == staff_id).values(**values))


async def clear_staff_totp(db: AsyncSession, staff_id: UUID) -> None:
    await db.execute(
        update(StaffMember)
        .where(StaffMember.id == staff_id)
        .values(totp_secret_encrypted=None, totp_enrolled_at=None, totp_last_step=None)
    )


async def revoke_all_staff_sessions(db: AsyncSession, staff_id: UUID, now: datetime) -> None:
    await db.execute(
        update(StaffSession)
        .where(StaffSession.staff_id == staff_id, StaffSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def create_staff_session(
    db: AsyncSession, staff_id: UUID, expires_at: datetime, now: datetime
) -> UUID:
    session = StaffSession(staff_id=staff_id, created_at=now, expires_at=expires_at)
    db.add(session)
    await db.flush()
    return session.id


async def extend_staff_session(db: AsyncSession, session_id: UUID, expires_at: datetime) -> None:
    await db.execute(
        update(StaffSession).where(StaffSession.id == session_id).values(expires_at=expires_at)
    )


async def revoke_staff_session(db: AsyncSession, session_id: UUID, now: datetime) -> None:
    await db.execute(
        update(StaffSession)
        .where(StaffSession.id == session_id, StaffSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def add_staff_refresh_token(
    db: AsyncSession, session_id: UUID, token_hash: str, now: datetime
) -> None:
    db.add(StaffRefreshToken(token_hash=token_hash, session_id=session_id, created_at=now))
    await db.flush()


@dataclass(frozen=True)
class StaffRefreshTokenRecord:
    session_id: UUID
    staff_id: UUID
    # Read at every refresh, so a new role or a disabled account reaches the next access token.
    role: StaffRole
    staff_disabled: bool
    state: RefreshTokenState


async def get_staff_refresh_token_for_update(
    db: AsyncSession, token_hash: str
) -> StaffRefreshTokenRecord | None:
    # The same row locking as for clients, see get_refresh_token_for_update.
    row = (
        await db.execute(
            select(StaffRefreshToken, StaffSession, StaffMember)
            .join(StaffSession, StaffRefreshToken.session_id == StaffSession.id)
            .join(StaffMember, StaffSession.staff_id == StaffMember.id)
            .where(StaffRefreshToken.token_hash == token_hash)
            .with_for_update(of=[StaffRefreshToken, StaffSession])
        )
    ).one_or_none()
    if row is None:
        return None
    token, session, staff = row
    return StaffRefreshTokenRecord(
        session_id=session.id,
        staff_id=staff.id,
        role=StaffRole(staff.role),
        staff_disabled=staff.disabled_at is not None,
        state=RefreshTokenState(
            used_at=token.used_at,
            session_revoked=session.revoked_at is not None,
            session_expires_at=session.expires_at,
        ),
    )


async def mark_staff_refresh_token_used(db: AsyncSession, token_hash: str, now: datetime) -> None:
    await db.execute(
        update(StaffRefreshToken)
        .where(StaffRefreshToken.token_hash == token_hash)
        .values(used_at=now)
    )
