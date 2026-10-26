from datetime import datetime
from uuid import UUID, uuid4

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.core.geo import SRID_WGS84
from flagman.fleet.domain import (
    BRAND_MAX_LENGTH,
    MODEL_NAME_MAX_LENGTH,
    PHOTO_URL_MAX_LENGTH,
    PLATE_MAX_LENGTH,
    VIN_LENGTH,
    CarStatus,
)

STATUS_LENGTH = 16

_STATE_COLUMNS = (
    "location",
    "battery_pct",
    "doors_locked",
    "ignition_on",
    "engine_locked",
    "state_at",
)


class CarModel(Base):
    __tablename__ = "car_models"
    __table_args__ = (
        UniqueConstraint("brand", "name", name="uq_car_models_brand_name"),
        CheckConstraint("seats > 0", name="seats_positive"),
        CheckConstraint("battery_wh > 0", name="battery_wh_positive"),
        CheckConstraint("range_km > 0", name="range_km_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    brand: Mapped[str] = mapped_column(String(BRAND_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(MODEL_NAME_MAX_LENGTH))
    seats: Mapped[int] = mapped_column(SmallInteger)
    battery_wh: Mapped[int]
    # Nominal range from the maker's sheet; the range left now is estimated from the charge.
    range_km: Mapped[int]
    photo_url: Mapped[str | None] = mapped_column(String(PHOTO_URL_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Car(Base):
    __tablename__ = "cars"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{status}'" for status in CarStatus) + ")",
            name="status_known",
        ),
        CheckConstraint("battery_pct BETWEEN 0 AND 100", name="battery_pct_range"),
        # A snapshot comes whole: a car either has no state yet or has all of it.
        CheckConstraint(
            "("
            + " AND ".join(f"{column} IS NULL" for column in _STATE_COLUMNS)
            + ") OR ("
            + " AND ".join(f"{column} IS NOT NULL" for column in _STATE_COLUMNS)
            + ")",
            name="state_complete",
        ),
        # The client map looks cars up by area and distance (#94).
        Index("ix_cars_location", "location", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(ForeignKey("car_models.id"), index=True)
    plate: Mapped[str] = mapped_column(String(PLATE_MAX_LENGTH), unique=True)
    vin: Mapped[str] = mapped_column(String(VIN_LENGTH), unique=True)
    # The car's id at the telematics provider: telemetry and commands are matched by it.
    telematics_id: Mapped[UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))

    location: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=SRID_WGS84, spatial_index=False)
    )
    battery_pct: Mapped[int | None] = mapped_column(SmallInteger)
    doors_locked: Mapped[bool | None]
    ignition_on: Mapped[bool | None]
    engine_locked: Mapped[bool | None]
    # The car's time of the snapshot, not when it arrived: telemetry can come late or twice.
    state_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
