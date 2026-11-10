"""grant table rights to the app role

Revision ID: af60e487a348
Revises: f2b9a6d4c871
Create Date: 2026-11-10 12:00:00.000000

The API and the worker log in as a member of flagman_app, which infra/postgres/bootstrap-roles.sh
creates (docs/adr/0031). The schema owner runs this, so the default privileges below cover every
table a later migration creates. A new append-only table must revoke UPDATE, DELETE and TRUNCATE
in its own migration, as here; tests/test_db_roles.py finds one that does not.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "af60e487a348"
down_revision: str | Sequence[str] | None = "f2b9a6d4c871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'flagman_app') THEN
            RAISE EXCEPTION 'role flagman_app is missing: run bootstrap-roles.sh first'
              USING HINT = 'infra/postgres/bootstrap-roles.sh, docs/adr/0031';
          END IF;
        END
        $$
        """
    )
    # Only what the owner owns: PostGIS's spatial_ref_sys is the superuser's and readable by all.
    op.execute(
        """
        DO $$
        DECLARE
          obj record;
        BEGIN
          FOR obj IN
            SELECT format('%I.%I', schemaname, tablename) AS name
            FROM pg_tables
            WHERE schemaname = 'public' AND tableowner = current_user
          LOOP
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO flagman_app', obj.name);
          END LOOP;
          FOR obj IN
            SELECT format('%I.%I', schemaname, sequencename) AS name
            FROM pg_sequences
            WHERE schemaname = 'public' AND sequenceowner = current_user
          LOOP
            EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO flagman_app', obj.name);
          END LOOP;
        END
        $$
        """
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO flagman_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO flagman_app"
    )
    # The migrations' own bookkeeping: the app has no business moving it.
    op.execute("REVOKE ALL ON alembic_version FROM flagman_app")
    # Rows never changed after insert. The triggers reject a change for every role, the owner
    # included; the missing right stops the app role before a trigger is reached.
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON "
        "audit_events, rental_receipts, rental_receipt_lines, rental_adjustments, "
        "client_block_events "
        "FROM flagman_app"
    )


def downgrade() -> None:
    # Back to the single role from before #167: the API then logs in as the superuser again, which
    # needs stand.env changed by hand (docs/08-demo-stand.md, «Откат», the rollback to before #167).
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM flagman_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM flagman_app"
    )
    op.execute(
        """
        DO $$
        DECLARE
          obj record;
        BEGIN
          FOR obj IN
            SELECT format('%I.%I', schemaname, tablename) AS name
            FROM pg_tables
            WHERE schemaname = 'public' AND tableowner = current_user
          LOOP
            EXECUTE format('REVOKE ALL ON %s FROM flagman_app', obj.name);
          END LOOP;
          FOR obj IN
            SELECT format('%I.%I', schemaname, sequencename) AS name
            FROM pg_sequences
            WHERE schemaname = 'public' AND sequenceowner = current_user
          LOOP
            EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM flagman_app', obj.name);
          END LOOP;
        END
        $$
        """
    )
