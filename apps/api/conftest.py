import base64
import logging
import secrets
import threading
from collections.abc import Iterator, MutableMapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

import pytest
import structlog
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from redis.asyncio import Redis
from starlette.testclient import WebSocketTestSession
from structlog.testing import LogCapture
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from flagman.core import alerts
from flagman.core.alerts import SpikeMeter
from flagman.core.config import Settings
from tools import container_guard
from tools.compose_images import compose_image

pytest_plugins = ("pytester",)

# Read from infra/docker-compose.yml, so tests run against what developers run locally.
POSTGRES_IMAGE = compose_image("postgres")
REDIS_IMAGE = compose_image("redis")

ALEMBIC_INI = Path(__file__).parent / "alembic.ini"
# The same script and the same three roles as infra/docker-compose.yml (docs/adr/0031): the tests
# run as the app role, so a query the API is not allowed to make fails here too.
BOOTSTRAP_ROLES_SCRIPT = Path(__file__).parents[2] / "infra" / "postgres" / "bootstrap-roles.sh"
BOOTSTRAP_ROLES_IN_CONTAINER = "/docker-entrypoint-initdb.d/20-flagman-roles.sh"
POSTGRES_SUPERUSER = "flagman"
POSTGRES_OWNER_USER = "flagman_owner"
POSTGRES_APP_USER = "flagman_api"


def pytest_configure(config: pytest.Config) -> None:
    # Registered here rather than listed in pytest_plugins, which would warn that the module,
    # already imported for the fixtures below, cannot have its asserts rewritten.
    config.pluginmanager.register(container_guard)


class SettingsFactory(Protocol):
    def __call__(self, **overrides: Any) -> Settings: ...


@pytest.fixture(scope="session")
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
def postgres_container(request: pytest.FixtureRequest) -> Iterator[PostgresContainer]:
    container_guard.refuse_unmarked_test(request)
    container = (
        PostgresContainer(
            POSTGRES_IMAGE,
            username=POSTGRES_SUPERUSER,
            password=secrets.token_hex(16),
            dbname="flagman",
            driver=None,
        )
        .with_env("FLAGMAN_OWNER_USER", POSTGRES_OWNER_USER)
        .with_env("FLAGMAN_OWNER_PASSWORD", secrets.token_hex(16))
        .with_env("FLAGMAN_APP_USER", POSTGRES_APP_USER)
        .with_env("FLAGMAN_APP_PASSWORD", secrets.token_hex(16))
        .with_volume_mapping(str(BOOTSTRAP_ROLES_SCRIPT), BOOTSTRAP_ROLES_IN_CONTAINER, "ro")
    )
    with container:
        yield container


@pytest.fixture(scope="session")
def redis_container(request: pytest.FixtureRequest) -> Iterator[RedisContainer]:
    container_guard.refuse_unmarked_test(request)
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
        "POSTGRES_USER": POSTGRES_APP_USER,
        "POSTGRES_PASSWORD": postgres_container.env["FLAGMAN_APP_PASSWORD"],
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
def migrated_database(
    integration_env: dict[str, str], postgres_container: PostgresContainer
) -> None:
    # migrations/env.py reads its settings from the environment, as the schema owner.
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, value in integration_env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("POSTGRES_OWNER_USER", POSTGRES_OWNER_USER)
        monkeypatch.setenv(
            "POSTGRES_OWNER_PASSWORD", postgres_container.env["FLAGMAN_OWNER_PASSWORD"]
        )
        command.upgrade(Config(ALEMBIC_INI), "head")


@pytest.fixture
def integration_settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    return make_settings(**{name.lower(): value for name, value in integration_env.items()})


@pytest.fixture
def owner_settings(
    integration_settings: Settings, postgres_container: PostgresContainer
) -> Settings:
    """The schema owner's connection: past the app role's rights, for checks of the triggers."""
    return integration_settings.model_copy(
        update={
            "postgres_user": POSTGRES_OWNER_USER,
            "postgres_password": SecretStr(postgres_container.env["FLAGMAN_OWNER_PASSWORD"]),
        }
    )


@pytest.fixture
def broken_spike_meter(integration_settings: Settings) -> SpikeMeter:
    """A spike meter whose Redis turns down every command, as if Redis were down."""
    # A wrong password fails at once, while an unreachable host would wait for a timeout.
    redis = Redis(
        host=integration_settings.redis_host,
        port=integration_settings.redis_port,
        password=secrets.token_hex(8),
    )
    return SpikeMeter(redis)


def capture_logs_of(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType
) -> list[MutableMapping[str, Any]]:
    """What the module's `logger` logs, as capture_logs would show it.

    capture_logs misses loggers that were used before it: the app caches them on first use.
    """
    capture = LogCapture()
    logger = structlog.wrap_logger(
        structlog.ReturnLogger(), processors=[capture], wrapper_class=structlog.BoundLogger
    )
    monkeypatch.setattr(module, "logger", logger)
    return capture.entries


@pytest.fixture
def alert_logs(monkeypatch: pytest.MonkeyPatch) -> list[MutableMapping[str, Any]]:
    """What SpikeMeter logs."""
    return capture_logs_of(monkeypatch, alerts)


@dataclass(frozen=True)
class CapturedLogs:
    alerts: list[MutableMapping[str, Any]]
    caplog: pytest.LogCaptureFixture

    def text(self) -> str:
        """Everything the app logged: the spike warnings and every other record."""
        return repr(self.alerts) + self.caplog.text


@pytest.fixture
def captured_logs(
    alert_logs: list[MutableMapping[str, Any]], caplog: pytest.LogCaptureFixture
) -> CapturedLogs:
    caplog.set_level(logging.DEBUG)
    return CapturedLogs(alert_logs, caplog)


def receive_json(socket: WebSocketTestSession, timeout: float) -> dict[str, Any]:
    """The next message of a test WebSocket, or TimeoutError.

    The test client has no receive timeout: a missing message would hang the whole run. A daemon
    thread that nobody joins, because a pool would wait on leaving for that very message.
    """
    received: Future[dict[str, Any]] = Future()

    def read() -> None:
        try:
            received.set_result(socket.receive_json())
        except Exception as error:
            received.set_exception(error)

    threading.Thread(target=read, daemon=True).start()
    return received.result(timeout)
