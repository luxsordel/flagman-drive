import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import service
from flagman.identity.dependencies import CurrentStaff, get_current_staff
from flagman.identity.domain import AccessClaims, StaffRole, issue_access_token

pytestmark = pytest.mark.integration

URL = "/admin/staff"
PASSWORD = "correct horse battery staple"


def _make_client(settings: Settings, role: StaffRole | None) -> TestClient:
    app = create_app(settings)
    if role is not None:
        # Real staff tokens are covered in test_staff_sign_in.py.
        signed_in = CurrentStaff(staff_id=uuid.uuid4(), role=role)
        app.dependency_overrides[get_current_staff] = lambda: signed_in
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def admin(integration_settings: Settings) -> Iterator[TestClient]:
    with _make_client(integration_settings, StaffRole.ADMIN) as client:
        yield client


def _create_staff(settings: Settings, email: str, *, disabled: bool = False) -> uuid.UUID:
    async def run() -> uuid.UUID:
        engine = create_engine(settings)
        try:
            factory = create_session_factory(engine)
            now = datetime.now(UTC)
            async with factory() as db:
                staff_id = await service.create_staff(db, email, PASSWORD, StaffRole.OPERATOR, now)
            if disabled:
                async with factory() as db:
                    await service.disable_staff(db, email, now)
            return staff_id
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_admin_sees_every_staff_member_by_email(
    admin: TestClient, integration_settings: Settings
) -> None:
    # Staff stay in the shared database between tests: a fresh prefix finds this test's own.
    prefix = f"list-{uuid.uuid4().hex[:8]}"
    second = _create_staff(integration_settings, f"{prefix}-b@example.com", disabled=True)
    first = _create_staff(integration_settings, f"{prefix}-a@example.com")

    response = admin.get(URL)

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Cache-Control"] == "no-store"
    ours = [item for item in response.json()["items"] if item["email"].startswith(prefix)]
    assert ours == [
        {"id": str(first), "email": f"{prefix}-a@example.com", "disabled": False},
        {"id": str(second), "email": f"{prefix}-b@example.com", "disabled": True},
    ]


@pytest.mark.parametrize("role", [StaffRole.OPERATOR, StaffRole.TECHNICIAN])
def test_only_admin_sees_the_list(integration_settings: Settings, role: StaffRole) -> None:
    with _make_client(integration_settings, role) as client:
        response = client.get(URL)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["type"] == "urn:flagman:problem:forbidden"
    assert "@" not in response.text


def test_clients_and_anonymous_callers_are_refused(integration_settings: Settings) -> None:
    client_token = issue_access_token(
        AccessClaims(user_id=uuid.uuid4(), session_id=uuid.uuid4()),
        integration_settings.api_jwt_secret.get_secret_value(),
        datetime.now(UTC),
    )

    with _make_client(integration_settings, role=None) as client:
        responses = [
            client.get(URL),
            client.get(URL, headers={"Authorization": "Bearer something"}),
            # A valid client token is still not a staff token.
            client.get(URL, headers={"Authorization": f"Bearer {client_token}"}),
        ]

    for response in responses:
        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert response.json()["type"] == "urn:flagman:problem:access-token-invalid"
