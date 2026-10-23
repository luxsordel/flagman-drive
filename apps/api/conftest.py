import base64
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from flagman.core.config import Settings

# Same images as infra/docker-compose.yml, so tests run against what developers run locally.
POSTGRES_IMAGE = "postgis/postgis:16-3.6"
REDIS_IMAGE = "redis:7.4.11-alpine"

ALEMBIC_INI = Path(__file__).parent / "alembic.ini"


class SettingsFactory(Protocol):
    def __call__(self, **overrides: Any) -> Settings: ...


@pytest.fixture
def make_settings() -> SettingsFactory:
    """Settings that never read the developer's .env file."""

    def make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "api_env": "test",
            "postgres_user": "test",
            "postgres_password": "test",
            "postgres_db": "test",
            "redis_password": "test",
            "api_jwt_secret": "test-jwt-secret-" + "j" * 32,
            "api_otp_pepper": "test-otp-pepper-" + "p" * 32,
            "api_totp_encryption_key": base64.b64encode(b"t" * 32).decode(),
            **overrides,
        }
        return Settings(_env_file=None, **values)

    return make


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username="flagman",
        password=secrets.token_hex(16),
        dbname="flagman",
        driver=None,
    )
    with container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer(REDIS_IMAGE, password=secrets.token_hex(16)) as container:
        yield container


@pytest.fixture(scope="session")
def integration_env(
    postgres_container: PostgresContainer, redis_container: RedisContainer
) -> dict[str, str]:
    """Environment variables pointing the API at the test containers."""
    return {
        "API_ENV": "test",
        "POSTGRES_HOST": postgres_container.get_container_host_ip(),
        "POSTGRES_PORT": str(postgres_container.get_exposed_port(5432)),
        "POSTGRES_USER": postgres_container.username,
        "POSTGRES_PASSWORD": postgres_container.password,
        "POSTGRES_DB": postgres_container.dbname,
        "REDIS_HOST": redis_container.get_container_host_ip(),
        "REDIS_PORT": str(redis_container.get_exposed_port(redis_container.port)),
        "REDIS_PASSWORD": str(redis_container.password),
        "API_JWT_SECRET": secrets.token_hex(32),
        "API_OTP_PEPPER": secrets.token_hex(32),
        "API_TOTP_ENCRYPTION_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "SMS_BACKEND": "log",
    }


@pytest.fixture(scope="session")
def migrated_database(integration_env: dict[str, str]) -> None:
    # migrations/env.py reads the same settings as the app, i.e. from the environment.
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, value in integration_env.items():
            monkeypatch.setenv(name, value)
        command.upgrade(Config(ALEMBIC_INI), "head")


@pytest.fixture
def integration_settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    return make_settings(**{name.lower(): value for name, value in integration_env.items()})
