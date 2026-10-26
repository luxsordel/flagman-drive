from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base

_PRICE_COLUMNS = (
    "driving_per_minute",
    "parked_per_minute",
    "waiting_per_minute",
    "free_waiting_minutes",
    "hold_amount",
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
            for column in _PRICE_COLUMNS
        ),
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
