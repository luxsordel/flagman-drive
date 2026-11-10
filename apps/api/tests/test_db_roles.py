"""The database roles of docs/adr/0031, checked on the roles the tests themselves run as."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from testcontainers.community.postgres import PostgresContainer

from conftest import (
    BOOTSTRAP_ROLES_IN_CONTAINER,
    POSTGRES_APP_USER,
    POSTGRES_OWNER_USER,
    POSTGRES_SUPERUSER,
)
from flagman.core.config import Settings
from flagman.core.db import create_engine

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
APP_ROLE = "flagman_app"

# Tables whose rows are never changed: each has a trigger named <table>_no_update_delete.
APPEND_ONLY_TABLES = (
    "audit_events",
    "client_block_events",
    "rental_adjustments",
    "rental_receipt_lines",
    "rental_receipts",
)


@pytest.fixture
def superuser_settings(
    integration_settings: Settings, postgres_container: PostgresContainer
) -> Settings:
    return integration_settings.model_copy(
        update={
            "postgres_user": POSTGRES_SUPERUSER,
            "postgres_password": SecretStr(postgres_container.password),
        }
    )


async def _connect(settings: Settings) -> AsyncIterator[AsyncConnection]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
async def app_db(integration_settings: Settings) -> AsyncIterator[AsyncConnection]:
    async for connection in _connect(integration_settings):
        yield connection


@pytest.fixture
async def owner_db(owner_settings: Settings) -> AsyncIterator[AsyncConnection]:
    async for connection in _connect(owner_settings):
        yield connection


@pytest.fixture
async def superuser_db(superuser_settings: Settings) -> AsyncIterator[AsyncConnection]:
    async for connection in _connect(superuser_settings):
        yield connection


async def _public_tables(connection: AsyncConnection) -> list[str]:
    rows = await connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    )
    return list(rows.scalars())


async def _app_may(connection: AsyncConnection, table: str, privilege: str) -> bool:
    allowed = await connection.scalar(
        text("SELECT has_table_privilege(:role, :table, :privilege)"),
        {"role": APP_ROLE, "table": f"public.{table}", "privilege": privilege},
    )
    return bool(allowed)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET id = id WHERE false",
        "DELETE FROM audit_events WHERE false",
        "TRUNCATE audit_events",
        "UPDATE client_block_events SET id = id WHERE false",
        "DELETE FROM client_block_events WHERE false",
        "TRUNCATE client_block_events",
        "UPDATE rental_adjustments SET id = id WHERE false",
        "DELETE FROM rental_adjustments WHERE false",
        "TRUNCATE rental_adjustments",
        "UPDATE rental_receipt_lines SET id = id WHERE false",
        "DELETE FROM rental_receipt_lines WHERE false",
        "TRUNCATE rental_receipt_lines",
        "UPDATE rental_receipts SET rental_id = rental_id WHERE false",
        "DELETE FROM rental_receipts WHERE false",
        "TRUNCATE rental_receipts",
    ],
)
async def test_the_app_cannot_change_append_only_rows(
    app_db: AsyncConnection, statement: str
) -> None:
    # Before the trigger: the right is missing, so even a statement that matches no row fails.
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_db.execute(text(statement))


async def test_every_append_only_table_is_closed_to_the_app(owner_db: AsyncConnection) -> None:
    # A new journal gets the trigger and, without its own REVOKE, the default UPDATE and DELETE.
    rows = await owner_db.execute(
        text(
            "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND t.tgname = c.relname || '_no_update_delete' "
            "ORDER BY c.relname"
        )
    )
    triggered = list(rows.scalars())

    assert triggered == list(APPEND_ONLY_TABLES)
    for table in triggered:
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not await _app_may(owner_db, table, privilege), f"{privilege} on {table}"
        assert await _app_may(owner_db, table, "INSERT")


async def test_the_app_reads_and_writes_every_other_table(owner_db: AsyncConnection) -> None:
    skipped = {*APPEND_ONLY_TABLES, "alembic_version", "spatial_ref_sys"}

    for table in await _public_tables(owner_db):
        if table in skipped:
            continue
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await _app_may(owner_db, table, privilege), f"{privilege} on {table}"


async def test_the_app_cannot_move_the_schema_version(app_db: AsyncConnection) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_db.execute(text("UPDATE alembic_version SET version_num = version_num"))


@pytest.mark.parametrize(
    "statement",
    [
        # The threat of docs/adr/0010: take the trigger off a journal, then rewrite it.
        "DROP TRIGGER audit_events_no_update_delete ON audit_events",
        "ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete",
        "DROP TABLE audit_events",
        "CREATE TABLE intruder (id int)",
        "CREATE TEMP TABLE intruder (id int)",
    ],
)
async def test_the_app_cannot_change_the_schema(app_db: AsyncConnection, statement: str) -> None:
    with pytest.raises(DBAPIError, match=r"permission denied|must be owner"):
        await app_db.execute(text(statement))


async def test_a_table_of_a_later_migration_is_open_to_the_app(owner_db: AsyncConnection) -> None:
    # Default privileges of the owner: the next migration's table needs no GRANT of its own.
    transaction = await owner_db.begin()
    try:
        await owner_db.execute(text("CREATE TABLE later_table (id serial PRIMARY KEY)"))

        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await _app_may(owner_db, "later_table", privilege), privilege
    finally:
        await transaction.rollback()


@pytest.mark.parametrize(
    ("role", "member_of_app"), [(POSTGRES_OWNER_USER, False), (POSTGRES_APP_USER, True)]
)
async def test_the_login_roles_are_not_privileged(
    owner_db: AsyncConnection, role: str, member_of_app: bool
) -> None:
    row = (
        await owner_db.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, "
                "pg_has_role(rolname, 'flagman_app', 'MEMBER') AS member_of_app "
                "FROM pg_roles WHERE rolname = :role"
            ),
            {"role": role},
        )
    ).one()

    assert not row.rolsuper
    assert not row.rolcreatedb
    assert not row.rolcreaterole
    assert not row.rolbypassrls
    assert not row.rolreplication
    assert row.member_of_app is member_of_app


async def test_the_app_is_not_the_owner(app_db: AsyncConnection) -> None:
    is_owner_member = await app_db.scalar(
        text("SELECT pg_has_role(current_user, :owner, 'MEMBER')"), {"owner": POSTGRES_OWNER_USER}
    )

    assert is_owner_member is False


def test_migrations_refuse_a_superuser(
    integration_env: dict[str, str], postgres_container: PostgresContainer, migrated_database: None
) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, value in integration_env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("POSTGRES_OWNER_USER", POSTGRES_SUPERUSER)
        monkeypatch.setenv("POSTGRES_OWNER_PASSWORD", postgres_container.password)

        with pytest.raises(RuntimeError, match="not a superuser"):
            command.upgrade(Config(ALEMBIC_INI), "head")


def test_the_downgrade_takes_the_rights_back(
    integration_env: dict[str, str], migrated_database: None, owner_settings: Settings
) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, value in integration_env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("POSTGRES_OWNER_USER", POSTGRES_OWNER_USER)
        monkeypatch.setenv(
            "POSTGRES_OWNER_PASSWORD", owner_settings.postgres_password.get_secret_value()
        )
        config = Config(ALEMBIC_INI)

        command.downgrade(config, "f2b9a6d4c871")
        try:
            may_select, default_acls = asyncio.run(_app_rights_after_downgrade(owner_settings))
        finally:
            # The rest of the session runs on the head schema.
            command.upgrade(config, "head")

    assert may_select is False
    # Otherwise a table created after the downgrade would still open to the app.
    assert default_acls == 0


async def _app_rights_after_downgrade(settings: Settings) -> tuple[bool, int]:
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            may_select = await _app_may(connection, "cars", "SELECT")
            default_acls = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_default_acl d, aclexplode(d.defaclacl) a "
                    "WHERE a.grantee = 'flagman_app'::regrole"
                )
            )
            return may_select, int(default_acls or 0)
    finally:
        await engine.dispose()


async def test_bootstrap_again_takes_back_restored_objects(
    superuser_db: AsyncConnection, owner_db: AsyncConnection, postgres_container: PostgresContainer
) -> None:
    # What `pg_restore --no-owner` as the superuser leaves, and what the stand has before #167:
    # every kind of object a migration creates, owned by the superuser.
    async with superuser_db.begin():
        await superuser_db.execute(text("CREATE TABLE restored_table (id serial PRIMARY KEY)"))
        await superuser_db.execute(text("CREATE SEQUENCE restored_sequence"))
        await superuser_db.execute(text("CREATE TYPE restored_enum AS ENUM ('a', 'b')"))
        await superuser_db.execute(
            text(
                "CREATE FUNCTION restored_function() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NULL; END $$"
            )
        )
    try:
        exit_code, output = postgres_container.get_wrapped_container().exec_run(
            ["bash", BOOTSTRAP_ROLES_IN_CONTAINER]
        )
        assert exit_code == 0, output.decode()

        rows = await owner_db.execute(
            text(
                "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c "
                "WHERE c.relname IN "
                "('restored_table', 'restored_table_id_seq', 'restored_sequence') "
                "UNION ALL SELECT t.typname, pg_get_userbyid(t.typowner) FROM pg_type t "
                "WHERE t.typname = 'restored_enum' "
                "UNION ALL SELECT p.proname, pg_get_userbyid(p.proowner) FROM pg_proc p "
                "WHERE p.proname = 'restored_function'"
            )
        )
        owners = {row[0]: row[1] for row in rows}
        assert owners == {
            "restored_table": POSTGRES_OWNER_USER,
            "restored_table_id_seq": POSTGRES_OWNER_USER,
            "restored_sequence": POSTGRES_OWNER_USER,
            "restored_enum": POSTGRES_OWNER_USER,
            "restored_function": POSTGRES_OWNER_USER,
        }
    finally:
        async with superuser_db.begin():
            await superuser_db.execute(text("DROP TABLE IF EXISTS restored_table"))
            await superuser_db.execute(text("DROP SEQUENCE IF EXISTS restored_sequence"))
            await superuser_db.execute(text("DROP TYPE IF EXISTS restored_enum"))
            await superuser_db.execute(text("DROP FUNCTION IF EXISTS restored_function()"))


def test_bootstrap_refuses_a_renamed_owner(postgres_container: PostgresContainer) -> None:
    # The default privileges stay with the old owner: a new one would leave the next migration's
    # tables closed to the app.
    exit_code, output = postgres_container.get_wrapped_container().exec_run(
        ["bash", BOOTSTRAP_ROLES_IN_CONTAINER],
        environment={"FLAGMAN_OWNER_USER": "flagman_owner_renamed"},
    )

    assert exit_code != 0
    assert b"the owner cannot be renamed" in output
    roles = postgres_container.get_wrapped_container().exec_run(
        [
            "psql",
            "--username",
            POSTGRES_SUPERUSER,
            "--dbname",
            postgres_container.dbname,
            "--tuples-only",
            "--command",
            "SELECT count(*) FROM pg_roles WHERE rolname = 'flagman_owner_renamed'",
        ]
    )
    assert roles.output.strip() == b"0"
