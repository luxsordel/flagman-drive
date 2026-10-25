from datetime import datetime
from uuid import UUID, uuid4

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from flagman.core.db import Base
from flagman.core.geo import SRID_WGS84
from flagman.geo.domain import ZONE_NAME_MAX_LENGTH, ZoneKind

KIND_LENGTH = 16


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{kind}'" for kind in ZoneKind) + ")", name="kind_known"
        ),
        # The end of every rental and every client map request look zones up by a point.
        Index("ix_zones_area", "area", postgresql_using="gist"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(ZONE_NAME_MAX_LENGTH))
    kind: Mapped[str] = mapped_column(String(KIND_LENGTH))
    area: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POLYGON", srid=SRID_WGS84, spatial_index=False)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Zones are disabled, not deleted: audit events and ended rentals keep pointing at them.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
