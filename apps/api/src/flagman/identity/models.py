from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base

PHONE_LENGTH = 16  # E.164 is at most 15 digits plus "+"
HASH_LENGTH = 64  # hex SHA-256
EMAIL_LENGTH = 254
PASSWORD_HASH_LENGTH = 255  # argon2 encoded hash with parameters, about 100 characters today
ROLE_LENGTH = 16
NAME_LENGTH = 100  # NAME_MAX_LENGTH in domain
LANGUAGE_LENGTH = 2


class User(Base):
    """A client; the profile fields stay empty until the client fills them in (#33)."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("language IN ('ru', 'en')", name="language_known"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    phone: Mapped[str] = mapped_column(String(PHONE_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_name: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    last_name: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    birth_date: Mapped[date | None] = mapped_column(Date)
    # Not unique: a contact address, not a way to sign in.
    email: Mapped[str | None] = mapped_column(String(EMAIL_LENGTH))
    language: Mapped[str] = mapped_column(String(LANGUAGE_LENGTH), server_default="ru")
    # Set by an operator (#112); a blocked client may not reserve or open cars.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingOtp(Base):
    """At most one code per phone: a new request replaces the previous one."""

    __tablename__ = "pending_otps"

    phone: Mapped[str] = mapped_column(String(PHONE_LENGTH), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    attempts: Mapped[int]
    # Indexed for the periodic cleanup, as are the ends of sessions below.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """One signed-in device; lives while its refresh tokens keep rotating."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_id_device_id", "user_id", "device_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    device_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RefreshToken(Base):
    """Every token ever issued in a session, so that a reused old one is recognized."""

    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffMember(Base):
    """An employee who signs in to the admin panel; created with `flagman-staff create`."""

    __tablename__ = "staff_members"
    __table_args__ = (
        CheckConstraint("role IN ('operator', 'technician', 'admin')", name="role_known"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(
            "totp_enrolled_at IS NULL OR totp_secret_encrypted IS NOT NULL",
            name="enrolled_has_totp_secret",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(EMAIL_LENGTH), unique=True)
    password_hash: Mapped[str] = mapped_column(String(PASSWORD_HASH_LENGTH))
    role: Mapped[str] = mapped_column(String(ROLE_LENGTH))
    # Encrypted with API_TOTP_ENCRYPTION_KEY. Set without totp_enrolled_at while the setup waits
    # for its first code; a new setup request replaces it.
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The time step of the last accepted code, so the same code is never accepted twice.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffSession(Base):
    """One admin panel sign-in; lives while its refresh tokens keep rotating."""

    __tablename__ = "staff_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_members.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class StaffRefreshToken(Base):
    """Every token ever issued in a staff session, so that a reused old one is recognized."""

    __tablename__ = "staff_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_sessions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
