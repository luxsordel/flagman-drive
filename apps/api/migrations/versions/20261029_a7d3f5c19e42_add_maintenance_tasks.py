"""add maintenance tasks

Revision ID: a7d3f5c19e42
Revises: e5b7c2d9a4f1
Create Date: 2026-10-29 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3f5c19e42"
down_revision: str | Sequence[str] | None = "e5b7c2d9a4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN = "status IN ('assigned', 'in_progress', 'new')"


def upgrade() -> None:
    op.create_table(
        "maintenance_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("car_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('assigned', 'cancelled', 'done', 'in_progress', 'new')",
            name=op.f("ck_maintenance_tasks_status_known"),
        ),
        sa.CheckConstraint(
            "type IN ('charging', 'inspection', 'other', 'repair', 'washing')",
            name=op.f("ck_maintenance_tasks_type_known"),
        ),
        sa.CheckConstraint(
            "status IN ('new', 'cancelled') OR assignee_id IS NOT NULL",
            name=op.f("ck_maintenance_tasks_assignee_set_once_assigned"),
        ),
        sa.CheckConstraint(
            "(status IN ('done', 'cancelled')) = (finished_at IS NOT NULL)",
            name=op.f("ck_maintenance_tasks_finished_at_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["staff_members.id"],
            name=op.f("fk_maintenance_tasks_assignee_id_staff_members"),
        ),
        sa.ForeignKeyConstraint(
            ["car_id"], ["cars.id"], name=op.f("fk_maintenance_tasks_car_id_cars")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["staff_members.id"],
            name=op.f("fk_maintenance_tasks_created_by_staff_members"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_tasks")),
    )
    op.create_index(
        op.f("ix_maintenance_tasks_assignee_id"), "maintenance_tasks", ["assignee_id"], unique=False
    )
    op.create_index(
        op.f("ix_maintenance_tasks_car_id"), "maintenance_tasks", ["car_id"], unique=False
    )
    op.create_index(
        op.f("ix_maintenance_tasks_created_at"), "maintenance_tasks", ["created_at"], unique=False
    )
    op.create_index(
        "uq_maintenance_tasks_open_car_type",
        "maintenance_tasks",
        ["car_id", "type"],
        unique=True,
        postgresql_where=sa.text(_OPEN),
    )
    op.create_index(
        "ix_maintenance_tasks_open_status",
        "maintenance_tasks",
        ["status"],
        unique=False,
        postgresql_where=sa.text(_OPEN),
    )

    op.create_table(
        "maintenance_task_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["maintenance_tasks.id"],
            name=op.f("fk_maintenance_task_photos_task_id_maintenance_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_task_photos")),
        sa.UniqueConstraint("object_key", name=op.f("uq_maintenance_task_photos_object_key")),
    )
    op.create_index(
        op.f("ix_maintenance_task_photos_task_id"),
        "maintenance_task_photos",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_maintenance_task_photos_task_id"), table_name="maintenance_task_photos")
    op.drop_table("maintenance_task_photos")
    op.drop_index(
        "ix_maintenance_tasks_open_status",
        table_name="maintenance_tasks",
        postgresql_where=sa.text(_OPEN),
    )
    op.drop_index(
        "uq_maintenance_tasks_open_car_type",
        table_name="maintenance_tasks",
        postgresql_where=sa.text(_OPEN),
    )
    op.drop_index(op.f("ix_maintenance_tasks_created_at"), table_name="maintenance_tasks")
    op.drop_index(op.f("ix_maintenance_tasks_car_id"), table_name="maintenance_tasks")
    op.drop_index(op.f("ix_maintenance_tasks_assignee_id"), table_name="maintenance_tasks")
    op.drop_table("maintenance_tasks")
