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
