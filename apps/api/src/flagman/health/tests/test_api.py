from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings


def test_health_does_not_touch_dependencies(make_settings: SettingsFactory) -> None:
    # Nothing listens on these ports; liveness must not care.
    settings = make_settings(postgres_port=1, redis_port=1)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_ready_when_database_and_redis_are_up(integration_settings: Settings) -> None:
    with TestClient(create_app(integration_settings)) as client:
        response = client.get("/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.integration
def test_not_ready_when_redis_is_down(integration_settings: Settings) -> None:
    settings = integration_settings.model_copy(update={"redis_port": 1})

    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json() == {"status": "fail", "checks": {"database": "ok", "redis": "fail"}}


@pytest.mark.integration
def test_not_ready_when_database_rejects_credentials(integration_settings: Settings) -> None:
    settings = integration_settings.model_copy(update={"postgres_user": "intruder"})

    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["checks"] == {"database": "fail", "redis": "ok"}
    # The failure reason stays in the logs; clients only see which check failed.
    assert "intruder" not in response.text
