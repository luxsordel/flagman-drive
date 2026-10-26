"""add car engine_locked

Revision ID: 7d4e1a9c2b63
Revises: 5b8e2f6a9d14
Create Date: 2026-10-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d4e1a9c2b63"
down_revision: str | Sequence[str] | None = "5b8e2f6a9d14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATE_COLUMNS = ("location", "battery_pct", "doors_locked", "ignition_on", "state_at")
_NEW_STATE_COLUMNS = (
    "location",
    "battery_pct",
    "doors_locked",
    "ignition_on",
    "engine_locked",
    "state_at",
)


def _state_complete(columns: Sequence[str]) -> str:
    return (
        "("
        + " AND ".join(f"{column} IS NULL" for column in columns)
        + ") OR ("
        + " AND ".join(f"{column} IS NOT NULL" for column in columns)
        + ")"
    )


def upgrade() -> None:
    op.add_column("cars", sa.Column("engine_locked", sa.Boolean(), nullable=True))
    # Nothing could lock an engine before commands existed, and the next snapshot overwrites it.
    op.execute("UPDATE cars SET engine_locked = false WHERE state_at IS NOT NULL")
    op.drop_constraint(op.f("ck_cars_state_complete"), "cars", type_="check")
    op.create_check_constraint(
        op.f("ck_cars_state_complete"), "cars", _state_complete(_NEW_STATE_COLUMNS)
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_cars_state_complete"), "cars", type_="check")
    op.drop_column("cars", "engine_locked")
    op.create_check_constraint(
        op.f("ck_cars_state_complete"), "cars", _state_complete(_OLD_STATE_COLUMNS)
    )
