from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection

from flagman.core.config import Settings
from flagman.core.db import Base, create_engine


@pytest.mark.integration
async def test_migrations_enable_postgis(integration_settings: Settings) -> None:
    engine = create_engine(integration_settings)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT postgis_version()"))
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()

    assert version
    assert revision


@pytest.mark.integration
async def test_migrations_match_the_models(integration_settings: Settings) -> None:
    # Migrations are reviewed by hand; this catches a model change that forgot its migration.
    # Importing the models registers their tables on Base.metadata.
    import flagman.audit.models  # noqa: PLC0415
    import flagman.identity.models  # noqa: PLC0415
    import flagman.kyc.models  # noqa: F401, PLC0415

    # PostGIS ships its own tables (spatial_ref_sys and others); only ours are compared.
    ours = set(Base.metadata.tables)

    def include_name(name: str | None, type_: str, parent_names: object) -> bool:
        return type_ != "table" or name in ours

    def diff(connection: Connection) -> list[Any]:
        context = MigrationContext.configure(connection, opts={"include_name": include_name})
        differences: list[Any] = compare_metadata(context, Base.metadata)
        return differences

    engine = create_engine(integration_settings)
    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(diff)
    finally:
        await engine.dispose()

    assert differences == []
