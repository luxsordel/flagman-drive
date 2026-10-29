import json

import pytest
from fastapi.testclient import TestClient

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.resources import Resources


def test_redis_timeout_does_not_depend_on_the_readiness_timeout(
    make_settings: SettingsFactory,
) -> None:
    # The Redis client is shared by the whole process (rate limits, locks, jobs), so the short
    # readiness probe timeout must not cap every Redis call.
    app = create_app(make_settings(readiness_timeout_seconds=0.5, redis_timeout_seconds=5.0))

    with TestClient(app):
        resources: Resources = app.state.resources
        connection_kwargs = resources.redis.connection_pool.connection_kwargs

    assert connection_kwargs["socket_timeout"] == 5.0
    assert connection_kwargs["socket_connect_timeout"] == 5.0


def _startup_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    return [line for line in lines if line.get("event") == "api_not_in_prod_mode"]


@pytest.mark.parametrize("api_env", ["dev", "test"])
def test_start_outside_prod_warns_about_disabled_protections(
    make_settings: SettingsFactory, capsys: pytest.CaptureFixture[str], api_env: str
) -> None:
    # A pilot server started without API_ENV=prod must be visible in its log.
    create_app(make_settings(api_env=api_env))

    events = _startup_events(capsys)
    assert len(events) == 1
    assert events[0]["level"] == "warning"
    assert events[0]["api_env"] == api_env
    assert events[0]["disabled_protections"] == ["hsts", "hidden_api_docs"]


@pytest.mark.parametrize("api_env", ["stand", "prod"])
def test_start_on_a_public_server_does_not_warn(
    make_settings: SettingsFactory, capsys: pytest.CaptureFixture[str], api_env: str
) -> None:
    create_app(make_settings(api_env=api_env))

    assert _startup_events(capsys) == []
