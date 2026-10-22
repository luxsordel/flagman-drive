import pytest
from sqlalchemy import text

from flagman.core.config import Settings
from flagman.core.db import create_engine


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
