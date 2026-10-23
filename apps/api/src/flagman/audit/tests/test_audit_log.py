import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service
from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import (
    SYSTEM,
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditEventFilter,
    AuditTarget,
    RequestOrigin,
)
from flagman.audit.models import AuditEvent
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory, get_session
from flagman.core.middleware import REQUEST_ID_HEADER
from flagman.core.security import keyed_hash
from flagman.identity.dependencies import CurrentStaff, get_current_staff
from flagman.identity.domain import StaffRole

pytestmark = pytest.mark.integration

URL = "/admin/audit-events"


@pytest.fixture
async def db(integration_settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(integration_settings)
    try:
        async with create_session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
def staff_id() -> uuid.UUID:
    # A new actor per test: events stay in the shared database between tests.
    return uuid.uuid4()


def _staff(staff_id: uuid.UUID) -> Actor:
    return Actor(ActorType.STAFF, staff_id)


async def _count(db: AsyncSession, staff_id: uuid.UUID) -> int:
    async with db.begin():
        count = await db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.actor_id == staff_id)
        )
    return count or 0


def _make_client(settings: Settings, role: StaffRole | None) -> TestClient:
    app = create_app(settings)
    if role is not None:
        signed_in = CurrentStaff(staff_id=uuid.uuid4(), role=role)
        app.dependency_overrides[get_current_staff] = lambda: signed_in
    _add_recording_route(app)
    return TestClient(app, base_url="https://testserver")


def _add_recording_route(app: FastAPI) -> None:
    @app.post("/test/audit/{staff_id}")
    async def record_event(
        staff_id: uuid.UUID,
        origin: Annotated[RequestOrigin, Depends(get_request_origin)],
        db: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        async with db.begin():
            entry = AuditEntry(_staff(staff_id), AuditAction.STAFF_SIGNED_IN, origin)
            await service.record(db, entry)


@pytest.fixture
def admin(integration_settings: Settings) -> Iterator[TestClient]:
    with _make_client(integration_settings, StaffRole.ADMIN) as client:
        yield client


async def test_record_commits_with_the_callers_transaction(
    db: AsyncSession, staff_id: uuid.UUID
) -> None:
    target = AuditTarget("kyc_document", uuid.uuid4())
    async with db.begin():
        await service.record(
            db,
            AuditEntry(
                _staff(staff_id),
                AuditAction.STAFF_SIGNED_IN,
                RequestOrigin(),
                target=target,
                details={"document_id": target.id, "approved": True},
            ),
        )

    async with db.begin():
        event = await db.scalar(select(AuditEvent).where(AuditEvent.actor_id == staff_id))
    assert event is not None
    assert event.action == AuditAction.STAFF_SIGNED_IN
    assert (event.target_type, event.target_id) == ("kyc_document", target.id)
    assert event.details == {"document_id": str(target.id), "approved": True}
    assert event.occurred_at.tzinfo is not None


async def test_events_of_one_transaction_keep_their_order(
    db: AsyncSession, staff_id: uuid.UUID
) -> None:
    async with db.begin():
        for step in range(3):
            await service.record(
                db,
                AuditEntry(
                    _staff(staff_id),
                    AuditAction.STAFF_SIGNED_IN,
                    RequestOrigin(),
                    details={"step": step},
                ),
            )

    async with db.begin():
        page = await service.list_events(db, AuditEventFilter(actor_id=staff_id), None, 10)

    assert [item.details["step"] for item in page.items] == [2, 1, 0]
    moments = [item.occurred_at for item in page.items]
    assert moments == sorted(set(moments), reverse=True)


async def test_rolled_back_action_leaves_no_event(db: AsyncSession, staff_id: uuid.UUID) -> None:
    transaction = await db.begin()
    await service.record(
        db, AuditEntry(_staff(staff_id), AuditAction.STAFF_SIGNED_IN, RequestOrigin())
    )
    await transaction.rollback()

    assert await _count(db, staff_id) == 0


async def test_record_refuses_to_run_outside_a_transaction(
    db: AsyncSession, staff_id: uuid.UUID
) -> None:
    entry = AuditEntry(_staff(staff_id), AuditAction.STAFF_SIGNED_IN, RequestOrigin())

    with pytest.raises(RuntimeError, match="inside the caller's transaction"):
        await service.record(db, entry)


async def test_record_rejects_personal_data_in_details(
    db: AsyncSession, staff_id: uuid.UUID
) -> None:
    entry = AuditEntry(
        _staff(staff_id),
        AuditAction.STAFF_SIGN_IN_FAILED,
        RequestOrigin(),
        details={"login": "operator@example.com"},
    )

    with pytest.raises(ValueError, match="looks like a phone number or an email"):
        async with db.begin():
            await service.record(db, entry)

    assert await _count(db, staff_id) == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'tampered' WHERE actor_id = :staff_id",
        "DELETE FROM audit_events WHERE actor_id = :staff_id",
        "TRUNCATE audit_events",
    ],
)
async def test_database_rejects_changing_events(
    db: AsyncSession, staff_id: uuid.UUID, statement: str
) -> None:
    async with db.begin():
        await service.record(
            db, AuditEntry(_staff(staff_id), AuditAction.STAFF_SIGNED_IN, RequestOrigin())
        )

    # Raw SQL on purpose: the code has no way to change events, the database must refuse too.
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin():
            await db.execute(text(statement), {"staff_id": staff_id})

    assert await _count(db, staff_id) == 1


async def test_system_events_have_no_actor(db: AsyncSession) -> None:
    request_id = uuid.uuid4()
    async with db.begin():
        await service.record(
            db,
            AuditEntry(SYSTEM, AuditAction.STAFF_SIGN_IN_FAILED, RequestOrigin(None, request_id)),
        )

    async with db.begin():
        event = await db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert event is not None
    assert (event.actor_type, event.actor_id) == ("system", None)


def test_listing_needs_a_signed_in_staff_member(integration_settings: Settings) -> None:
    # Real staff tokens are covered in identity/tests/test_staff_sign_in.py.
    with _make_client(integration_settings, role=None) as client:
        anonymous = client.get(URL)
        with_token = client.get(URL, headers={"Authorization": "Bearer something"})

    for response in (anonymous, with_token):
        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert response.json()["type"] == "urn:flagman:problem:access-token-invalid"


@pytest.mark.parametrize("role", [StaffRole.OPERATOR, StaffRole.TECHNICIAN])
def test_only_admin_reads_the_log(integration_settings: Settings, role: StaffRole) -> None:
    with _make_client(integration_settings, role) as client:
        response = client.get(URL)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["type"] == "urn:flagman:problem:forbidden"


def test_admin_sees_who_did_what_and_from_where_without_the_address(
    admin: TestClient, staff_id: uuid.UUID, integration_settings: Settings
) -> None:
    recorded = admin.post(f"/test/audit/{staff_id}")
    assert recorded.status_code == HTTPStatus.OK

    response = admin.get(URL, params={"actor_id": str(staff_id)})

    assert response.status_code == HTTPStatus.OK
    [event] = response.json()["items"]
    assert event["actor_type"] == "staff"
    assert event["actor_id"] == str(staff_id)
    assert event["action"] == "staff.signed_in"
    assert event["request_id"] == recorded.headers[REQUEST_ID_HEADER]
    # TestClient connects as "testclient": stored only as a keyed hash.
    pepper = integration_settings.api_otp_pepper.get_secret_value()
    assert event["ip_hash"] == keyed_hash("testclient", pepper)
    assert "testclient" not in response.text
    assert response.json()["next_cursor"] is None


async def _insert_at(
    db: AsyncSession, staff_id: uuid.UUID, action: AuditAction, occurred_at: datetime
) -> None:
    # Only tests set the time: the service always leaves it to the database.
    async with db.begin():
        await db.execute(
            insert(AuditEvent).values(
                actor_type="staff",
                actor_id=staff_id,
                action=action,
                occurred_at=occurred_at,
                details={},
            )
        )


async def test_filters_by_action_and_period(
    admin: TestClient, db: AsyncSession, staff_id: uuid.UUID
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for hours, action in [
        (0, AuditAction.STAFF_SIGNED_IN),
        (1, AuditAction.STAFF_SIGN_IN_FAILED),
        (2, AuditAction.STAFF_SIGNED_IN),
        (3, AuditAction.STAFF_SIGNED_IN),
    ]:
        await _insert_at(db, staff_id, action, start + timedelta(hours=hours))

    def occurred(**params: Any) -> list[str]:
        response = admin.get(URL, params={"actor_id": str(staff_id), **params})
        assert response.status_code == HTTPStatus.OK
        return [item["occurred_at"] for item in response.json()["items"]]

    assert len(occurred()) == 4
    assert len(occurred(action="staff.sign_in_failed")) == 1
    # From is inclusive, to is exclusive.
    period = occurred(
        occurred_from=(start + timedelta(hours=1)).isoformat(),
        occurred_to=(start + timedelta(hours=3)).isoformat(),
    )
    assert [datetime.fromisoformat(value) for value in period] == [
        start + timedelta(hours=2),
        start + timedelta(hours=1),
    ]
    assert occurred(action="staff.signed_in", occurred_to=(start + timedelta(hours=1)).isoformat())


async def test_pages_go_from_newest_to_oldest_without_gaps(
    admin: TestClient, db: AsyncSession, staff_id: uuid.UUID
) -> None:
    start = datetime(2026, 2, 1, tzinfo=UTC)
    # Two events share a moment: the id breaks the tie, so neither is lost between pages.
    moments = [start, start + timedelta(minutes=1), start + timedelta(minutes=1)]
    moments += [start + timedelta(minutes=2), start + timedelta(minutes=3)]
    for moment in moments:
        await _insert_at(db, staff_id, AuditAction.STAFF_SIGNED_IN, moment)

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"actor_id": str(staff_id), "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = admin.get(URL, params=params).json()
        seen += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == len(set(seen)) == len(moments)
    full = admin.get(URL, params={"actor_id": str(staff_id), "limit": 100}).json()["items"]
    assert [item["id"] for item in full] == seen


@pytest.mark.parametrize(
    "params",
    [
        {"occurred_from": "2026-10-23T12:00:00"},
        {"occurred_from": "2026-10-23T12:00:00Z", "occurred_to": "2026-10-23T12:00:00Z"},
        {"occurred_from": "2026-10-23T13:00:00+01:00", "occurred_to": "2026-10-23T11:00:00Z"},
        {"cursor": "garbage"},
        {"limit": 0},
        {"limit": 101},
        {"action": "rentals.deleted"},
        {"phone": "+375291234567"},
    ],
)
def test_rejects_invalid_queries(admin: TestClient, params: dict[str, Any]) -> None:
    response = admin.get(URL, params=params)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
