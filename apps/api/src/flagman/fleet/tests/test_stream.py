import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.fleet import api, service, stream
from flagman.fleet.domain import CarState, CarTransition, TelemetrySnapshot
from flagman.fleet.tests.conftest import FakeGateway, add_car, client_token, staff_token
from flagman.identity import service as identity
from flagman.identity.domain import STAFF_ACCESS_TOKEN_TTL, StaffRole

pytestmark = pytest.mark.integration

URL = "/admin/fleet/stream"
ORIGIN = "https://admin.flagman.test"
POINT = GeoPoint(lat=53.9023, lon=27.5615)
PASSWORD = "correct horse battery staple"
TIMEOUT_S = 10.0


@pytest.fixture
def settings(integration_settings: Settings) -> Settings:
    return integration_settings.model_copy(update={"api_cors_origins": [ORIGIN]})


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        deadline = time.monotonic() + TIMEOUT_S
        while not client.app.state.fleet_hub.listening:  # type: ignore[attr-defined]
            assert time.monotonic() < deadline, "the fleet hub never started listening"
            time.sleep(0.05)
        yield client


def _run[T](settings: Settings, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await work(db)
        finally:
            await engine.dispose()

    return asyncio.run(run())


class Staff:
    """A staff member with a real session, as the stream checks it in the database."""

    def __init__(self, settings: Settings, role: StaffRole = StaffRole.OPERATOR) -> None:
        self.settings = settings
        self.email = f"stream-{uuid.uuid4().hex[:12]}@example.com"
        now = datetime.now(UTC)
        self.id = _run(
            settings, lambda db: identity.create_staff(db, self.email, PASSWORD, role, now)
        )
        self.session_id = uuid.uuid4()
        self.role = role
        _run(settings, self._add_session)

    async def _add_session(self, db: AsyncSession) -> None:
        async with db.begin():
            await db.execute(
                text(
                    "INSERT INTO staff_sessions (id, staff_id, created_at, expires_at) "
                    "VALUES (:id, :staff_id, now(), now() + interval '12 hours')"
                ),
                {"id": self.session_id, "staff_id": self.id},
            )

    def token(self, now: datetime | None = None) -> str:
        return staff_token(
            self.settings, self.role, staff_id=self.id, session_id=self.session_id, now=now
        )

    def revoke_session(self) -> None:
        async def revoke(db: AsyncSession) -> None:
            async with db.begin():
                await db.execute(
                    text("UPDATE staff_sessions SET revoked_at = now() WHERE id = :id"),
                    {"id": self.session_id},
                )

        _run(self.settings, revoke)

    def disable(self) -> None:
        _run(self.settings, lambda db: identity.disable_staff(db, self.email, datetime.now(UTC)))


@pytest.fixture
def staff(settings: Settings) -> Staff:
    return Staff(settings)


def _connect(client: TestClient, origin: str | None = ORIGIN) -> Any:
    headers = {"origin": origin} if origin else {}
    return client.websocket_connect(URL, headers=headers)


def _receive(socket: WebSocketTestSession, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    # The test client has no receive timeout: a missing message would hang the whole run.
    with ThreadPoolExecutor(max_workers=1) as pool:
        message: dict[str, Any] = pool.submit(socket.receive_json).result(timeout)
    return message


def _read_until_closed(socket: WebSocketTestSession) -> None:
    while True:
        _receive(socket)


def _close_code(socket: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        _read_until_closed(socket)
    return closed.value.code


def _sign_in(socket: WebSocketTestSession, token: str) -> dict[str, Any]:
    socket.send_json({"type": "auth", "token": token})
    snapshot = _receive(socket)
    assert snapshot["type"] == "snapshot"
    return snapshot


def _until_car(socket: WebSocketTestSession, car_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        message = _receive(socket)
        assert message["type"] == "cars"
        for car in message["items"]:
            if car["id"] == car_id:
                car_out: dict[str, Any] = car
                return car_out
    raise AssertionError(f"no update of car {car_id}")


def _change_status(settings: Settings, car_id: str, transition: CarTransition) -> None:
    async def change(db: AsyncSession) -> None:
        async with db.begin():
            await service.change_status(db, uuid.UUID(car_id), transition, datetime.now(UTC))

    _run(settings, change)


def _telematics_id(settings: Settings, car_id: str) -> uuid.UUID:
    return _run(settings, lambda db: service.get_car(db, uuid.UUID(car_id))).telematics_id


def test_after_signing_in_staff_get_the_whole_fleet(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    car_id = add_car(settings, POINT, battery_pct=42)

    with _connect(client) as socket:
        snapshot = _sign_in(socket, staff.token())

    cars = {car["id"]: car for car in snapshot["items"]}
    assert cars[car_id]["state"]["battery_pct"] == 42
    assert snapshot["rules"]["low_battery_pct"] == settings.fleet_min_battery_pct
    assert "as_of" in snapshot


@pytest.mark.parametrize("role", list(StaffRole))
def test_every_staff_role_gets_the_stream(
    client: TestClient, settings: Settings, role: StaffRole
) -> None:
    with _connect(client) as socket:
        _sign_in(socket, Staff(settings, role).token())


def test_a_status_change_reaches_staff_after_the_commit(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    car_id = add_car(settings, POINT)

    with _connect(client) as socket:
        _sign_in(socket, staff.token())
        _change_status(settings, car_id, CarTransition.SEND_TO_SERVICE)
        car = _until_car(socket, car_id)

    assert car["status"] == "service"


def test_telemetry_reaches_staff(client: TestClient, settings: Settings, staff: Staff) -> None:
    car_id = add_car(settings, POINT, battery_pct=80)
    now = datetime.now(UTC)
    state = CarState(POINT, 63, False, True, False, now)
    snapshot = TelemetrySnapshot(_telematics_id(settings, car_id), state)

    with _connect(client) as socket:
        _sign_in(socket, staff.token())
        _run(
            settings,
            lambda db: service.receive_telemetry(db, [snapshot], now, timedelta(minutes=5)),
        )
        car = _until_car(socket, car_id)

    assert car["state"]["battery_pct"] == 63
    assert (car["state"]["doors_locked"], car["state"]["ignition_on"]) == (False, True)


def test_a_rolled_back_change_is_not_sent(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    rolled_back = add_car(settings, POINT)
    committed = add_car(settings, POINT)

    class AbortError(Exception):
        pass

    async def roll_back(db: AsyncSession) -> None:
        with contextlib.suppress(AbortError):
            async with db.begin():
                await service.change_status(
                    db, uuid.UUID(rolled_back), CarTransition.GO_OFFLINE, datetime.now(UTC)
                )
                raise AbortError

    with _connect(client) as socket:
        _sign_in(socket, staff.token())
        _run(settings, roll_back)
        _change_status(settings, committed, CarTransition.GO_OFFLINE)
        message = _receive(socket)

    # Notices are delivered in commit order: the rolled back one would have come first.
    assert [car["id"] for car in message["items"]] == [committed]


def test_a_command_reaches_every_staff_member(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    car_id = add_car(settings, POINT)
    opened = CarState(POINT, 70, False, False, False, datetime.now(UTC))
    client.app.dependency_overrides[api.get_telematics_gateway] = lambda: FakeGateway(opened)  # type: ignore[attr-defined]
    sender = Staff(settings, StaffRole.TECHNICIAN)

    with _connect(client) as socket:
        _sign_in(socket, staff.token())
        response = client.post(
            f"/admin/fleet/cars/{car_id}/commands",
            json={"command": "open", "comment": "Opening for a cleaning"},
            headers={"Authorization": f"Bearer {sender.token()}"},
        )
        assert response.status_code == 200, response.text
        car = _until_car(socket, car_id)
        command = _receive(socket)

    assert car["state"]["doors_locked"] is False
    assert command == {
        "type": "command",
        "command": {
            "command_id": response.json()["command_id"],
            "car_id": car_id,
            "command": "open",
            "outcome": "done",
            "reason": None,
            "staff_id": str(sender.id),
            "at": command["command"]["at"],
        },
    }


@pytest.mark.parametrize("origin", [None, "https://evil.example"], ids=["missing", "foreign"])
def test_a_socket_from_another_origin_is_refused(client: TestClient, origin: str | None) -> None:
    with pytest.raises(WebSocketDisconnect) as refused, _connect(client, origin):
        pass

    assert refused.value.code == stream.CLOSE_POLICY


def test_a_client_token_is_refused(client: TestClient, settings: Settings) -> None:
    with _connect(client) as socket:
        socket.send_json({"type": "auth", "token": client_token(settings)})

        assert _close_code(socket) == stream.CLOSE_UNAUTHORIZED


def test_a_token_without_a_live_session_is_refused(client: TestClient, settings: Settings) -> None:
    with _connect(client) as socket:
        socket.send_json({"type": "auth", "token": staff_token(settings, StaffRole.ADMIN)})

        assert _close_code(socket) == stream.CLOSE_UNAUTHORIZED


def test_a_disabled_staff_member_is_refused(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    staff.disable()

    with _connect(client) as socket:
        socket.send_json({"type": "auth", "token": staff.token()})

        assert _close_code(socket) == stream.CLOSE_UNAUTHORIZED


@pytest.mark.parametrize(
    "message",
    [{"type": "auth"}, {"type": "subscribe", "token": "x"}, {"type": "auth", "token": "x" * 5000}],
    ids=["no-token", "unknown-type", "too-long"],
)
def test_a_malformed_first_message_closes_the_socket(
    client: TestClient, message: dict[str, Any]
) -> None:
    with _connect(client) as socket:
        socket.send_text(json.dumps(message))

        assert _close_code(socket) == stream.CLOSE_POLICY


def test_a_binary_message_closes_the_socket(client: TestClient) -> None:
    with _connect(client) as socket:
        socket.send_bytes(b"\x00")

        assert _close_code(socket) == stream.CLOSE_POLICY


def test_without_a_token_in_time_the_socket_is_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "AUTH_TIMEOUT_S", 0.2)

    with _connect(client) as socket:
        assert _close_code(socket) == stream.CLOSE_UNAUTHORIZED


def _expiring_token(staff: Staff, seconds_left: float) -> str:
    return staff.token(
        now=datetime.now(UTC) - STAFF_ACCESS_TOKEN_TTL + timedelta(seconds=seconds_left)
    )


def test_the_socket_closes_when_the_token_expires(client: TestClient, staff: Staff) -> None:
    with _connect(client) as socket:
        _sign_in(socket, _expiring_token(staff, 2))

        assert _close_code(socket) == stream.CLOSE_UNAUTHORIZED


def test_a_renewed_token_keeps_the_socket_open(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    car_id = add_car(settings, POINT)

    with _connect(client) as socket:
        _sign_in(socket, _expiring_token(staff, 2))
        socket.send_json({"type": "auth", "token": staff.token()})
        time.sleep(3)
        _change_status(settings, car_id, CarTransition.SEND_TO_SERVICE)

        assert _until_car(socket, car_id)["status"] == "service"


def test_a_token_of_another_staff_member_closes_the_socket(
    client: TestClient, settings: Settings, staff: Staff
) -> None:
    with _connect(client) as socket:
        _sign_in(socket, staff.token())
        socket.send_json({"type": "auth", "token": Staff(settings).token()})

        assert _close_code(socket) == stream.CLOSE_POLICY


def test_the_socket_closes_when_the_session_is_revoked(
    client: TestClient, staff: Staff, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "SESSION_CHECK_INTERVAL_S", 0.2)

    with _connect(client) as socket:
        _sign_in(socket, staff.token())
        staff.revoke_session()

        assert _close_code(socket) == stream.CLOSE_UNAUTHORIZED


def test_the_token_is_not_logged(
    settings: Settings, staff: Staff, capsys: pytest.CaptureFixture[str]
) -> None:
    token = staff.token()
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        deadline = time.monotonic() + TIMEOUT_S
        while not client.app.state.fleet_hub.listening:  # type: ignore[attr-defined]
            assert time.monotonic() < deadline
            time.sleep(0.05)
        with _connect(client) as socket:
            _sign_in(socket, token)
        with _connect(client) as socket:
            socket.send_json({"type": "auth", "token": token + "broken"})
            _close_code(socket)

    output = capsys.readouterr()
    assert token not in output.out
    assert token not in output.err
