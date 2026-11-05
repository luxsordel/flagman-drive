from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base

# The hold is the only price that cannot be zero: a payment provider holds a positive amount.
_NOT_NEGATIVE_COLUMNS = (
    "driving_per_minute",
    "parked_per_minute",
    "waiting_per_minute",
    "free_waiting_minutes",
)


class Tariff(Base):
    """One per car model; its prices live in the versions."""

    __tablename__ = "tariffs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # A key on another module's table, as kyc does with users: a tariff of a missing model is
    # meaningless, and only the database guards that against a model deleted meanwhile.
    model_id: Mapped[UUID] = mapped_column(ForeignKey("car_models.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TariffVersion(Base):
    """Rows are only added: a rental points at the version it was booked with (docs/adr/0018)."""

    __tablename__ = "tariff_versions"
    __table_args__ = (
        UniqueConstraint("tariff_id", "number", name="uq_tariff_versions_tariff_id_number"),
        CheckConstraint("number > 0", name="number_positive"),
        *(
            CheckConstraint(f"{column} >= 0", name=f"{column}_not_negative")
            for column in _NOT_NEGATIVE_COLUMNS
        ),
        CheckConstraint("hold_amount > 0", name="hold_amount_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tariff_id: Mapped[UUID] = mapped_column(ForeignKey("tariffs.id"))
    number: Mapped[int]
    # Kopecks of BYN.
    driving_per_minute: Mapped[int]
    parked_per_minute: Mapped[int]
    waiting_per_minute: Mapped[int]
    free_waiting_minutes: Mapped[int]
    hold_amount: Mapped[int]
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The staff member who saved it; None for the demo seed. No key: staff accounts are disabled,
    # never deleted, and the audit log has the same id.
    created_by: Mapped[UUID | None]


class FineType(Base):
    """A kind of fine in the catalog; its names and amount live in the versions."""

    __tablename__ = "fine_types"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Stable: the code says what the fine is for, automatic fines are found by it.
    code: Mapped[str] = mapped_column(String(40), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FineTypeVersion(Base):
    """Rows are only added: a charged fine points at the version it was charged by."""

    __tablename__ = "fine_type_versions"
    __table_args__ = (
        UniqueConstraint(
            "fine_type_id", "number", name="uq_fine_type_versions_fine_type_id_number"
        ),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    fine_type_id: Mapped[UUID] = mapped_column(ForeignKey("fine_types.id"))
    number: Mapped[int]
    name_ru: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    # Kopecks of BYN.
    amount: Mapped[int]
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # As with tariff versions: the staff member, None for the demo seed.
    created_by: Mapped[UUID | None]
