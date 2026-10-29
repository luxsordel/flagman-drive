from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningDelete

from flagman.identity.domain import Language, OtpChallenge, RefreshTokenState, StaffRole
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


async def get_or_create_user(
    db: AsyncSession, phone: str, language: Language, now: datetime
) -> UUID:
    # ON CONFLICT instead of select-then-insert: two first sign-ins at once must not both insert,
    # and a returning client keeps their language.
    created: UUID | None = await db.scalar(
        insert(User)
        .values(phone=phone, language=language.value, created_at=now)
        .on_conflict_do_nothing(index_elements=[User.phone])
        .returning(User.id)
    )
    if created is not None:
        return created
    existing = await db.scalar(select(User.id).where(User.phone == phone))
    if existing is None:
        raise RuntimeError("user vanished between insert and select")
    return existing


@dataclass(frozen=True)
class ClientProfileRecord:
    phone: str
    first_name: str | None
    last_name: str | None
    birth_date: date | None
    email: str | None
    language: Language


PROFILE_FIELDS = frozenset({"first_name", "last_name", "birth_date", "email", "language"})


def _client_profile_record(row: User) -> ClientProfileRecord:
    return ClientProfileRecord(
        phone=row.phone,
        first_name=row.first_name,
        last_name=row.last_name,
        birth_date=row.birth_date,
        email=row.email,
        language=Language(row.language),
    )


async def get_client_profile(
    db: AsyncSession, user_id: UUID, *, for_update: bool = False
) -> ClientProfileRecord | None:
    query = select(User).where(User.id == user_id)
    if for_update:
        query = query.with_for_update()
    row = await db.scalar(query)
    return _client_profile_record(row) if row is not None else None


async def is_client_blocked(db: AsyncSession, user_id: UUID) -> bool:
    # FOR SHARE: a block that comes during the caller's transaction waits for it to end.
    blocked_at = await db.scalar(
        select(User.blocked_at).where(User.id == user_id).with_for_update(read=True)
    )
    return blocked_at is not None


async def get_client_profiles(
    db: AsyncSession, user_ids: Collection[UUID]
) -> dict[UUID, ClientProfileRecord]:
    rows = await db.scalars(select(User).where(User.id.in_(user_ids)))
    return {row.id: _client_profile_record(row) for row in rows}


async def update_client_profile(
    db: AsyncSession, user_id: UUID, changes: Mapping[str, str | date | None]
) -> ClientProfileRecord | None:
    """Sets only the given fields; None when the user does not exist."""
    # The phone is how the client signs in: it must never change through the profile.
    unknown = changes.keys() - PROFILE_FIELDS
    if unknown:
        raise ValueError(f"not profile fields: {sorted(unknown)}")
    if not changes:
        return await get_client_profile(db, user_id)
    row = await db.scalar(update(User).where(User.id == user_id).values(**changes).returning(User))
    return _client_profile_record(row) if row is not None else None


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


@dataclass(frozen=True)
class StaffSummary:
    id: UUID
    email: str
    disabled: bool


async def list_staff(db: AsyncSession) -> list[StaffSummary]:
    # Only these columns: a list has no business reading password hashes and TOTP secrets.
    rows = await db.execute(
        select(StaffMember.id, StaffMember.email, StaffMember.disabled_at).order_by(
            StaffMember.email
        )
    )
    return [StaffSummary(row.id, row.email, row.disabled_at is not None) for row in rows]


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


async def set_enrolled_totp_secret(
    db: AsyncSession, staff_id: UUID, ciphertext: bytes, now: datetime
) -> None:
    await db.execute(
        update(StaffMember)
        .where(StaffMember.id == staff_id)
        .values(totp_secret_encrypted=ciphertext, totp_enrolled_at=now, totp_last_step=None)
    )


async def accept_totp_step(
    db: AsyncSession, staff_id: UUID, step: int, enrolled_at: datetime | None
) -> None:
    """Stores the used step; `enrolled_at` is set only by the code that finishes the setup."""
    values: dict[str, object] = {"totp_last_step": step}
    if enrolled_at is not None:
        values["totp_enrolled_at"] = enrolled_at
    await db.execute(update(StaffMember).where(StaffMember.id == staff_id).values(**values))


async def disable_staff(db: AsyncSession, staff_id: UUID, now: datetime) -> None:
    await db.execute(
        update(StaffMember)
        .where(StaffMember.id == staff_id, StaffMember.disabled_at.is_(None))
        .values(disabled_at=now)
    )


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


async def is_staff_session_active(
    db: AsyncSession, staff_id: UUID, session_id: UUID, now: datetime
) -> bool:
    active = await db.scalar(
        select(StaffSession.id)
        .join(StaffMember, StaffSession.staff_id == StaffMember.id)
        .where(
            StaffSession.id == session_id,
            StaffSession.staff_id == staff_id,
            StaffSession.revoked_at.is_(None),
            StaffSession.expires_at > now,
            StaffMember.disabled_at.is_(None),
        )
    )
    return active is not None


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


# Well below Postgres' deadlock_timeout (1 s by default): when the cleanup and a refresh wait on
# each other, the cleanup gives up first, so the refresh never fails as the deadlock victim.
CLEANUP_LOCK_WAIT = timedelta(milliseconds=100)
# lock_not_available: a statement waited longer than lock_timeout.
_LOCK_TIMEOUT_SQLSTATE = "55P03"


class CleanupLockTimeoutError(Exception):
    """A row the cleanup needs stayed locked longer than CLEANUP_LOCK_WAIT."""


async def limit_lock_wait(db: AsyncSession) -> None:
    """For the rest of the transaction, a lock not free within CLEANUP_LOCK_WAIT fails."""
    # set_config(..., true) is SET LOCAL that takes a bound value.
    milliseconds = int(CLEANUP_LOCK_WAIT.total_seconds() * 1000)
    await db.execute(
        text("SELECT set_config('lock_timeout', :value, true)"), {"value": f"{milliseconds}ms"}
    )


async def _delete_count[T](db: AsyncSession, statement: ReturningDelete[T]) -> int:
    # Counted by Postgres: the deleted rows themselves never travel to the worker.
    count = select(func.count()).select_from(statement.cte("deleted"))
    try:
        deleted = await db.scalar(count)
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == _LOCK_TIMEOUT_SQLSTATE:
            raise CleanupLockTimeoutError from error
        raise
    return deleted or 0


async def delete_expired_otps(db: AsyncSession, now: datetime, limit: int) -> int:
    # SKIP LOCKED: a code someone is entering right now is left for the next run.
    batch = (
        select(PendingOtp.phone)
        .where(PendingOtp.expires_at < now)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return await _delete_count(
        db, delete(PendingOtp).where(PendingOtp.phone.in_(batch)).returning(PendingOtp.phone)
    )


async def delete_ended_sessions(db: AsyncSession, ended_before: datetime, limit: int) -> int:
    """Their refresh tokens go with them (ON DELETE CASCADE).

    A refresh locks the token and then its session, while this locks the session and then its
    tokens; run it under limit_lock_wait so that it is this side that gives up, not the refresh.
    A live session is never picked, however old its used tokens are: they are what proves a copied
    token is being replayed (docs/adr/0009).
    """
    batch = (
        select(AuthSession.id)
        .where(or_(AuthSession.expires_at < ended_before, AuthSession.revoked_at < ended_before))
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return await _delete_count(
        db, delete(AuthSession).where(AuthSession.id.in_(batch)).returning(AuthSession.id)
    )


async def delete_ended_staff_sessions(db: AsyncSession, ended_before: datetime, limit: int) -> int:
    """The same as delete_ended_sessions, for the admin panel."""
    batch = (
        select(StaffSession.id)
        .where(or_(StaffSession.expires_at < ended_before, StaffSession.revoked_at < ended_before))
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return await _delete_count(
        db, delete(StaffSession).where(StaffSession.id.in_(batch)).returning(StaffSession.id)
    )
