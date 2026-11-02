"""add support notifications

Revision ID: e9c2b7d4a6f3
Revises: b3f6d8a1c5e7
Create Date: 2026-11-02 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9c2b7d4a6f3"
down_revision: str | Sequence[str] | None = "b3f6d8a1c5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENTS = (
    "'payment_failed', 'reservation_expired', 'reservation_expiring', "
    "'trip_finished', 'trip_low_battery'"
)
_CATEGORIES = "'payment', 'reservation', 'trip'"


def _replace_checks(events: str, categories: str) -> None:
    op.drop_constraint(
        op.f("ck_notification_outbox_event_known"), "notification_outbox", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_outbox_event_known"), "notification_outbox", f"event IN ({events})"
    )
    op.drop_constraint(
        op.f("ck_notification_opt_outs_category_known"), "notification_opt_outs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_opt_outs_category_known"),
        "notification_opt_outs",
        f"category IN ({categories})",
    )


def upgrade() -> None:
    op.alter_column("notification_outbox", "rental_id", existing_type=sa.Uuid(), nullable=True)
    _replace_checks(f"{_EVENTS}, 'support_reply'", f"{_CATEGORIES}, 'support'")


def downgrade() -> None:
    op.execute("DELETE FROM notification_outbox WHERE event = 'support_reply'")
    op.execute("DELETE FROM notification_opt_outs WHERE category = 'support'")
    _replace_checks(_EVENTS, _CATEGORIES)
    op.alter_column("notification_outbox", "rental_id", existing_type=sa.Uuid(), nullable=False)
