import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ORM models register themselves on Base.metadata when imported: import each module's
# `models` here as modules get tables, otherwise autogenerate will not see them.
import flagman.audit.models
import flagman.fleet.models
import flagman.geo.models
import flagman.identity.models
import flagman.kyc.models
import flagman.maintenance.models
import flagman.notifications.models
import flagman.payments.models
import flagman.pricing.models
import flagman.rentals.models
import flagman.support.models  # noqa: F401
from flagman.core.config import load_migration_settings
from flagman.core.db import Base
from flagman.core.logging import configure_logging

target_metadata = Base.metadata

settings = load_migration_settings()
configure_logging(settings.api_log_level)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _refuse_superuser(connection: Connection) -> None:
    # A superuser would own what it creates, and the default privileges the app role relies on are
    # set for the owner only (docs/adr/0031): the API would lose access to every new table.
    is_superuser = connection.exec_driver_sql(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ).scalar_one()
    # Ends the transaction the query began: Alembic would join it and never commit the migrations.
    connection.rollback()
    if is_superuser:
        raise RuntimeError(
            "migrations must run as the schema owner (POSTGRES_OWNER_USER), not a superuser"
        )


def do_run_migrations(connection: Connection) -> None:
    _refuse_superuser(connection)
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(
        settings.database_url, poolclass=pool.NullPool, hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
