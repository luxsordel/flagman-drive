import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.identity.domain import (
    AccessClaims,
    StaffAccessClaims,
    StaffRole,
    issue_access_token,
    issue_staff_access_token,
)


@pytest.fixture
def client(integration_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


class Caller:
    def __init__(self, settings: Settings, role: StaffRole | None) -> None:
        self.id = uuid.uuid4()
        secret = settings.api_jwt_secret.get_secret_value()
        now = datetime.now(UTC)
        if role is None:
            token = issue_access_token(AccessClaims(self.id, uuid.uuid4()), secret, now)
        else:
            token = issue_staff_access_token(
                StaffAccessClaims(self.id, uuid.uuid4(), role), secret, now
            )
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(integration_settings: Settings) -> Caller:
    return Caller(integration_settings, StaffRole.ADMIN)


@pytest.fixture
def operator(integration_settings: Settings) -> Caller:
    return Caller(integration_settings, StaffRole.OPERATOR)
