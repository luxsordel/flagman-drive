"""Who may call what, written out by hand for every route of the API.

The table is deliberately not derived from the routes' dependencies: a test that reads the answer
from the code under test would approve any mistake in it. A new route fails
`test_every_route_is_listed` until somebody decides here who may call it.
"""

import base64
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import Future
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import anyio
import httpx2
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from conftest import SettingsFactory
from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.fleet import stream
from flagman.fleet.tests.conftest import Staff, bearer, client_token, staff_token
from flagman.identity.domain import AccessClaims, StaffRole, issue_access_token
from flagman.rentals.tests.conftest import insert_test_user

type RouteKey = tuple[str, str]

ADMIN_ONLY = frozenset({StaffRole.ADMIN})
OFFICE = frozenset({StaffRole.OPERATOR, StaffRole.ADMIN})
ALL_STAFF = frozenset(StaffRole)

STAFF_ROUTES: dict[RouteKey, frozenset[StaffRole]] = {
    # Audit
    ("GET", "/admin/audit-events"): ADMIN_ONLY,
    # Staff accounts
    ("GET", "/staff/me"): ALL_STAFF,
    ("GET", "/admin/staff"): ADMIN_ONLY,
    # Clients
    ("GET", "/admin/clients"): OFFICE,
    ("GET", "/admin/clients/{user_id}"): OFFICE,
    ("POST", "/admin/clients/{user_id}/block"): OFFICE,
    ("POST", "/admin/clients/{user_id}/unblock"): OFFICE,
    # KYC review
    ("GET", "/admin/kyc/applications"): OFFICE,
    ("GET", "/admin/kyc/applications/{application_id}"): OFFICE,
    ("GET", "/admin/kyc/applications/{application_id}/documents/{kind}/url"): OFFICE,
    ("POST", "/admin/kyc/applications/{application_id}/approve"): OFFICE,
    ("POST", "/admin/kyc/applications/{application_id}/reject"): OFFICE,
    # Fleet
    ("GET", "/admin/fleet/cars"): ALL_STAFF,
    ("GET", "/admin/fleet/cars/{car_id}"): ALL_STAFF,
    ("POST", "/admin/fleet/cars/{car_id}/commands"): OFFICE,
    # Zones
    ("GET", "/admin/zones"): ALL_STAFF,
    ("GET", "/admin/zones/{zone_id}"): ALL_STAFF,
    ("POST", "/admin/zones"): ADMIN_ONLY,
    ("PUT", "/admin/zones/{zone_id}"): ADMIN_ONLY,
    ("POST", "/admin/zones/{zone_id}/disable"): ADMIN_ONLY,
    # Maintenance
    ("GET", "/admin/maintenance/tasks"): ALL_STAFF,
    ("GET", "/admin/maintenance/technicians"): OFFICE,
    ("POST", "/admin/maintenance/tasks"): OFFICE,
    ("GET", "/admin/maintenance/tasks/{task_id}"): ALL_STAFF,
    ("POST", "/admin/maintenance/tasks/{task_id}/assign"): OFFICE,
    ("POST", "/admin/maintenance/tasks/{task_id}/start"): ALL_STAFF,
    ("POST", "/admin/maintenance/tasks/{task_id}/complete"): ALL_STAFF,
    ("POST", "/admin/maintenance/tasks/{task_id}/cancel"): OFFICE,
    ("POST", "/admin/maintenance/tasks/{task_id}/commands"): ALL_STAFF,
    ("POST", "/admin/maintenance/tasks/{task_id}/photos"): ALL_STAFF,
    ("GET", "/admin/maintenance/tasks/{task_id}/photos/{photo_id}/url"): ALL_STAFF,
    # Pricing
    ("GET", "/admin/tariffs"): OFFICE,
    ("GET", "/admin/tariffs/{tariff_id}"): OFFICE,
    ("GET", "/admin/tariffs/{tariff_id}/versions"): OFFICE,
    ("POST", "/admin/tariffs"): ADMIN_ONLY,
    ("PUT", "/admin/tariffs/{tariff_id}"): ADMIN_ONLY,
    ("GET", "/admin/fine-types"): OFFICE,
    ("GET", "/admin/fine-types/{fine_type_id}/versions"): OFFICE,
    ("POST", "/admin/fine-types"): ADMIN_ONLY,
    ("PUT", "/admin/fine-types/{fine_type_id}"): ADMIN_ONLY,
    # Rentals
    ("GET", "/admin/rentals"): OFFICE,
    ("GET", "/admin/rentals/{rental_id}"): OFFICE,
    ("GET", "/admin/rentals/{rental_id}/inspections/{stage}/photos/{photo_id}/url"): OFFICE,
    ("POST", "/admin/rentals/{rental_id}/force-finish"): OFFICE,
    ("POST", "/admin/rentals/{rental_id}/adjustments"): OFFICE,
    ("POST", "/admin/rentals/{rental_id}/refunds/retry"): OFFICE,
    # Support chat
    ("GET", "/admin/support/unread"): OFFICE,
    ("GET", "/admin/support/conversations"): OFFICE,
    ("GET", "/admin/support/conversations/{conversation_id}"): OFFICE,
    ("GET", "/admin/support/conversations/{conversation_id}/messages"): OFFICE,
    ("POST", "/admin/support/conversations/{conversation_id}/take"): OFFICE,
    ("POST", "/admin/support/conversations/{conversation_id}/messages"): OFFICE,
    ("POST", "/admin/support/conversations/{conversation_id}/close"): OFFICE,
    ("GET", "/admin/support/conversations/{conversation_id}/messages/{message_id}/photo-url"): (
        OFFICE
    ),
    # Incidents
    ("GET", "/admin/support/incidents"): OFFICE,
    ("GET", "/admin/support/incidents/{incident_id}"): OFFICE,
    ("POST", "/admin/support/incidents/{incident_id}/take"): OFFICE,
    ("POST", "/admin/support/incidents/{incident_id}/resolve"): OFFICE,
    ("POST", "/admin/support/incidents/{incident_id}/reject"): OFFICE,
    ("POST", "/admin/support/incidents/{incident_id}/send-to-service"): OFFICE,
    ("GET", "/admin/support/incidents/{incident_id}/photos/{photo_id}/url"): OFFICE,
    # Fines
    ("GET", "/admin/support/fines"): OFFICE,
    ("POST", "/admin/support/fines"): OFFICE,
    ("GET", "/admin/support/fines/{fine_id}"): OFFICE,
    ("POST", "/admin/support/fines/{fine_id}/cancel"): OFFICE,
    ("POST", "/admin/support/fines/{fine_id}/photos"): OFFICE,
    ("GET", "/admin/support/fines/{fine_id}/photos/{photo_id}/url"): OFFICE,
}

CLIENT_ROUTES: frozenset[RouteKey] = frozenset(
    {
        # Profile and notifications
        ("GET", "/me"),
        ("PATCH", "/me"),
        ("PUT", "/me/push-device"),
        ("DELETE", "/me/push-device"),
        ("GET", "/me/notification-preferences"),
        ("PATCH", "/me/notification-preferences"),
        # KYC
        ("GET", "/kyc/application"),
        ("PUT", "/kyc/documents/{kind}"),
        ("GET", "/kyc/documents/{kind}/url"),
        ("POST", "/kyc/application/submit"),
        # Map
        ("GET", "/map/cars"),
        ("GET", "/map/cars/{car_id}"),
        ("GET", "/map/zones"),
        # Cards
        ("GET", "/payment-cards"),
        ("POST", "/payment-cards/bindings"),
        ("GET", "/payment-cards/bindings/{binding_id}"),
        ("DELETE", "/payment-cards/{card_id}"),
        ("POST", "/payment-cards/{card_id}/default"),
        # Rentals
        ("POST", "/rentals"),
        ("GET", "/rentals/current"),
        ("GET", "/rentals/debts"),
        ("GET", "/rentals/history"),
        ("GET", "/rentals/{rental_id}"),
        ("POST", "/rentals/{rental_id}/cancel"),
        ("POST", "/rentals/{rental_id}/park"),
        ("POST", "/rentals/{rental_id}/resume"),
        ("POST", "/rentals/{rental_id}/finish"),
        ("GET", "/rentals/{rental_id}/receipt"),
        ("POST", "/rentals/{rental_id}/pay"),
        ("GET", "/rentals/{rental_id}/inspections/{stage}"),
        ("POST", "/rentals/{rental_id}/inspections/{stage}/photos"),
        ("GET", "/rentals/{rental_id}/inspections/{stage}/photos/{photo_id}/url"),
        ("POST", "/rentals/{rental_id}/inspections/{stage}/damages"),
        ("POST", "/rentals/{rental_id}/inspections/{stage}/submit"),
        ("POST", "/rentals/{rental_id}/commands"),
        # Support
        ("GET", "/support/messages"),
        ("GET", "/support/unread"),
        ("POST", "/support/messages"),
        ("POST", "/support/messages/photos"),
        ("GET", "/support/messages/{message_id}/photo-url"),
        ("POST", "/support/incidents"),
        ("GET", "/support/incidents"),
        ("GET", "/support/incidents/{incident_id}"),
        ("POST", "/support/incidents/{incident_id}/photos"),
        ("GET", "/support/incidents/{incident_id}/photos/{photo_id}/url"),
        ("GET", "/support/fines"),
        ("GET", "/support/fines/{fine_id}"),
        ("POST", "/support/fines/{fine_id}/pay"),
        ("GET", "/support/fines/{fine_id}/photos/{photo_id}/url"),
    }
)

# Listed one by one rather than by prefix: a new route under /auth/ needs its own decision too.
_NO_TOKEN_YET = "Sign-in: the caller has no access token yet"
_REFRESH_TOKEN = "Authenticated by the refresh token, which outlives the access token"
_CHALLENGE = "Sign-in: authenticated by the challenge from the previous step"
_DOCS = "Served only outside stand and prod, see test_app.py and test_middleware.py"
_CARD_PAGE = "Stands in for the provider's card page: a signed session link is the access"
EXEMPT_ROUTES: dict[RouteKey, str] = {
    ("GET", "/health"): "Liveness probe of the proxy and the orchestrator: no data",
    ("GET", "/ready"): "Readiness probe of the proxy and the orchestrator: no data",
    ("POST", "/auth/otp/request"): _NO_TOKEN_YET,
    ("POST", "/auth/otp/verify"): _NO_TOKEN_YET,
    ("POST", "/auth/token/refresh"): _REFRESH_TOKEN,
    ("POST", "/auth/logout"): _REFRESH_TOKEN,
    ("POST", "/staff/auth/password"): _NO_TOKEN_YET,
    ("POST", "/staff/auth/totp/enrollment"): _CHALLENGE,
    ("POST", "/staff/auth/totp"): _CHALLENGE,
    ("POST", "/staff/auth/token/refresh"): _REFRESH_TOKEN,
    ("POST", "/staff/auth/logout"): _REFRESH_TOKEN,
    ("POST", "/fleet/telemetry"): "Cars sign the body, see fleet/tests/test_telemetry_api.py",
    ("POST", "/payments/webhooks/fake"): "The provider signs it, see payments/tests/test_cards_api",
    ("GET", "/fake-provider/cards"): _CARD_PAGE,
    ("POST", "/fake-provider/cards"): _CARD_PAGE,
    ("GET", "/docs"): _DOCS,
    ("GET", "/docs/oauth2-redirect"): _DOCS,
    ("GET", "/openapi.json"): _DOCS,
}

# The one known case of a 422 ahead of the guard: FastAPI parses a JSON body before it runs any
# dependency, so these routes answer JSON that does not parse before they look at the caller. It
# shows an outsider which routes take a JSON body, and nothing of the schema or of the data.
UNPARSED_JSON_ROUTES: frozenset[RouteKey] = frozenset(
    {
        ("PATCH", "/me"),
        ("PATCH", "/me/notification-preferences"),
        ("PUT", "/me/push-device"),
        ("POST", "/payment-cards/bindings"),
        ("POST", "/rentals"),
        ("POST", "/rentals/{rental_id}/commands"),
        ("POST", "/rentals/{rental_id}/inspections/{stage}/damages"),
        ("POST", "/support/incidents"),
        ("POST", "/support/messages"),
        ("POST", "/admin/clients/{user_id}/block"),
        ("POST", "/admin/clients/{user_id}/unblock"),
        ("POST", "/admin/kyc/applications/{application_id}/approve"),
        ("POST", "/admin/kyc/applications/{application_id}/reject"),
        ("POST", "/admin/fleet/cars/{car_id}/commands"),
        ("POST", "/admin/maintenance/tasks"),
        ("POST", "/admin/maintenance/tasks/{task_id}/assign"),
        ("POST", "/admin/maintenance/tasks/{task_id}/cancel"),
        ("POST", "/admin/maintenance/tasks/{task_id}/commands"),
        ("POST", "/admin/maintenance/tasks/{task_id}/complete"),
        ("POST", "/admin/rentals/{rental_id}/adjustments"),
        ("POST", "/admin/rentals/{rental_id}/force-finish"),
        ("POST", "/admin/support/conversations/{conversation_id}/messages"),
        ("POST", "/admin/support/fines"),
        ("POST", "/admin/support/fines/{fine_id}/cancel"),
        ("POST", "/admin/support/incidents/{incident_id}/reject"),
        ("POST", "/admin/support/incidents/{incident_id}/resolve"),
        ("POST", "/admin/fine-types"),
        ("PUT", "/admin/fine-types/{fine_type_id}"),
        ("POST", "/admin/tariffs"),
        ("PUT", "/admin/tariffs/{tariff_id}"),
        ("POST", "/admin/zones"),
        ("PUT", "/admin/zones/{zone_id}"),
    }
)

# A WebSocket has no request to answer with a status: the token comes in the first message.
STREAM_ROUTE: RouteKey = ("WS", "/admin/fleet/stream")
STREAM_ROLES = ALL_STAFF

ORIGIN = "https://admin.flagman.test"
TIMEOUT_S = 10.0
_BAD_BODY = b"{bad"
_NOT_JSON_BODY_HEADERS: tuple[dict[str, str], ...] = (
    {},
    {"content-type": "text/plain"},
    {"content-type": "application/x-www-form-urlencoded"},
    {"content-type": "multipart/form-data; boundary=x"},
)
_PATH_PARAM = re.compile(r"{(\w+)}")
_NAMED_PARAMS = {"kind": "passport", "stage": "before"}


def _route_keys(app: FastAPI) -> set[RouteKey]:
    keys: set[RouteKey] = set()
    # app.routes holds whole included routers as single entries, so only this walk sees each route.
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        path = str(context.path)
        if isinstance(route, Route):
            methods = set(route.methods or ())
            # Starlette adds HEAD to every GET: it is the same endpoint, not a route of its own.
            if "GET" in methods:
                methods.discard("HEAD")
            keys |= {(method, path) for method in methods}
        elif isinstance(route, WebSocketRoute):
            keys.add(("WS", path))
        else:
            # A mount or anything else this walk cannot see into must not slip through unlisted.
            keys.add((type(route).__name__, path))
    return keys


def _listed_routes() -> set[RouteKey]:
    return {*STAFF_ROUTES, *CLIENT_ROUTES, *EXEMPT_ROUTES, STREAM_ROUTE}


def _url(path: str, *, junk: bool = False) -> str:
    def value(match: re.Match[str]) -> str:
        name = match[1]
        if junk:
            return "not-a-valid-value"
        if name.endswith("_id"):
            return str(uuid.uuid4())
        return _NAMED_PARAMS[name]

    return _PATH_PARAM.sub(value, path)


def _id(method: str, path: str, role: StaffRole | None = None) -> str:
    return f"{method} {path}" + (f" as {role.value}" if role else "")


STAFF_CASES = [pytest.param(*key, id=_id(*key)) for key in STAFF_ROUTES]
CLIENT_CASES = [pytest.param(*key, id=_id(*key)) for key in sorted(CLIENT_ROUTES)]
ALLOWED_CASES = [
    pytest.param(*key, role, id=_id(*key, role))
    for key, roles in STAFF_ROUTES.items()
    for role in sorted(roles)
]
DISALLOWED_CASES = [
    pytest.param(*key, role, id=_id(*key, role))
    for key, roles in STAFF_ROUTES.items()
    for role in sorted(ALL_STAFF - roles)
]


def _attempts(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> Iterator[httpx2.Response]:
    yield client.request(method, _url(path), headers=headers)
    # Bad ids and a body of the wrong shape must not be judged ahead of the caller: a 422 here would
    # tell an outsider the shape of an endpoint they may not use. UNPARSED_JSON_ROUTES lists the one
    # exception, and it has a test of its own.
    yield client.request(method, _url(path, junk=True), json={"unexpected": 1}, headers=headers)
    for body_headers in _NOT_JSON_BODY_HEADERS:
        yield client.request(
            method, _url(path), content=_BAD_BODY, headers={**headers, **body_headers}
        )


def _assert_refused(response: httpx2.Response, status: HTTPStatus, problem: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["type"] == f"urn:flagman:problem:{problem}", response.text


def _assert_unauthorized(attempts: Iterator[httpx2.Response]) -> None:
    for response in attempts:
        _assert_refused(response, HTTPStatus.UNAUTHORIZED, "access-token-invalid")


def _assert_past_the_guard(response: httpx2.Response) -> None:
    # The ids are made up and the body is missing, so 404 and 422 are the expected ends here:
    # both come after the caller was checked. Only the guard's own refusals would be a failure.
    assert response.status_code != HTTPStatus.UNAUTHORIZED, response.text
    if response.status_code == HTTPStatus.FORBIDDEN:
        assert response.json()["type"] != "urn:flagman:problem:forbidden", response.text
    assert response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR, response.text


@pytest.fixture(scope="module")
def offline_settings(make_settings: SettingsFactory) -> Settings:
    # Nothing listens on these ports. A refusal must not need the database, Redis or storage, so a
    # rate limit or a session lookup running ahead of the token check fails the test loudly instead
    # of quietly using up a window.
    return make_settings(
        postgres_host="127.0.0.1", postgres_port=1, redis_host="127.0.0.1", redis_port=1
    )


@pytest.fixture(scope="module")
def offline_client(offline_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(offline_settings), base_url="https://testserver") as client:
        yield client


@pytest.fixture(scope="module")
def live_settings(
    integration_env: dict[str, str], migrated_database: None, make_settings: SettingsFactory
) -> Settings:
    # Everything optional is configured so that an endpoint answers for itself, not with a 503 for
    # a missing setting. Nothing listens on these ports: the ids are made up, so no request gets as
    # far as storage or a car.
    return make_settings(
        **{name.lower(): value for name, value in integration_env.items()},
        api_cors_origins=[ORIGIN],
        s3_endpoint_url="http://127.0.0.1:1",
        s3_access_key="unused",
        s3_secret_key="unused",
        emulator_url="http://127.0.0.1:1",
        emulator_command_secret=secrets.token_hex(32),
        payments_provider="fake",
        payments_webhook_secret=secrets.token_hex(32),
        payments_token_encryption_key=base64.b64encode(secrets.token_bytes(32)).decode(),
    )


@pytest.fixture(scope="module")
def live_client(live_settings: Settings) -> Iterator[TestClient]:
    app = create_app(live_settings)
    with TestClient(app, base_url="https://testserver") as client:
        deadline = time.monotonic() + TIMEOUT_S
        while not app.state.fleet_hub.listening:
            assert time.monotonic() < deadline, "the fleet hub never started listening"
            time.sleep(0.05)
        yield client


@pytest.fixture(scope="module")
def staff_by_role(live_settings: Settings) -> dict[StaffRole, Staff]:
    return {role: Staff(live_settings, role) for role in StaffRole}


@pytest.fixture(scope="module")
def signed_in_client(live_settings: Settings) -> dict[str, str]:
    user_id = uuid.uuid4()
    insert_test_user(live_settings, user_id)
    secret = live_settings.api_jwt_secret.get_secret_value()
    return bearer(
        issue_access_token(AccessClaims(user_id, uuid.uuid4()), secret, datetime.now(UTC))
    )


def test_every_route_is_listed(offline_settings: Settings) -> None:
    routes = _route_keys(create_app(offline_settings))

    assert routes - _listed_routes() == set(), "new routes: decide who may call them"
    assert _listed_routes() - routes == set(), "the table lists routes that no longer exist"


def test_a_route_missing_from_the_table_is_noticed(offline_settings: Settings) -> None:
    app = create_app(offline_settings)

    async def endpoint() -> None: ...

    app.add_api_route("/admin/brand-new", endpoint)
    app.add_api_websocket_route("/admin/brand-new-stream", endpoint)
    app.mount("/brand-new-mount", FastAPI())

    assert _route_keys(app) - _listed_routes() == {
        ("GET", "/admin/brand-new"),
        ("WS", "/admin/brand-new-stream"),
        ("Mount", "/brand-new-mount"),
    }


def test_everything_under_admin_is_for_staff() -> None:
    not_for_staff = [*EXEMPT_ROUTES, *CLIENT_ROUTES]

    assert [path for _, path in not_for_staff if path.startswith("/admin")] == []


@pytest.mark.parametrize(("method", "path"), STAFF_CASES)
def test_a_staff_route_refuses_anonymous_callers(
    offline_client: TestClient, method: str, path: str
) -> None:
    _assert_unauthorized(_attempts(offline_client, method, path, {}))


@pytest.mark.parametrize(("method", "path"), STAFF_CASES)
def test_a_staff_route_refuses_a_client_token(
    offline_client: TestClient, offline_settings: Settings, method: str, path: str
) -> None:
    headers = bearer(client_token(offline_settings))

    _assert_unauthorized(_attempts(offline_client, method, path, headers))


@pytest.mark.parametrize(("method", "path", "role"), DISALLOWED_CASES)
def test_a_staff_route_refuses_a_role_it_does_not_list(
    offline_client: TestClient,
    offline_settings: Settings,
    method: str,
    path: str,
    role: StaffRole,
) -> None:
    headers = bearer(staff_token(offline_settings, role))

    for response in _attempts(offline_client, method, path, headers):
        _assert_refused(response, HTTPStatus.FORBIDDEN, "forbidden")


@pytest.mark.parametrize(("method", "path"), CLIENT_CASES)
def test_a_client_route_refuses_anonymous_callers(
    offline_client: TestClient, method: str, path: str
) -> None:
    _assert_unauthorized(_attempts(offline_client, method, path, {}))


@pytest.mark.parametrize("role", sorted(StaffRole))
@pytest.mark.parametrize(("method", "path"), CLIENT_CASES)
def test_a_client_route_refuses_a_staff_token(
    offline_client: TestClient,
    offline_settings: Settings,
    method: str,
    path: str,
    role: StaffRole,
) -> None:
    headers = bearer(staff_token(offline_settings, role))

    _assert_unauthorized(_attempts(offline_client, method, path, headers))


@pytest.mark.parametrize("content_type", ["application/json", "application/problem+json"])
def test_only_the_listed_routes_judge_unparsed_json_ahead_of_the_caller(
    offline_client: TestClient, content_type: str
) -> None:
    answered_first: set[RouteKey] = set()
    for method, path in [*STAFF_ROUTES, *CLIENT_ROUTES]:
        response = offline_client.request(
            method, _url(path), content=_BAD_BODY, headers={"content-type": content_type}
        )
        if response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
            answered_first.add((method, path))
            # Exactly this: one more word here could be the schema of an endpoint they may not use.
            assert response.json()["errors"] == [
                {"loc": ["body", 1], "message": "JSON decode error", "type": "json_invalid"}
            ]
        else:
            _assert_refused(response, HTTPStatus.UNAUTHORIZED, "access-token-invalid")

    assert answered_first == UNPARSED_JSON_ROUTES


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path", "role"), ALLOWED_CASES)
def test_a_staff_route_lets_a_listed_role_through(
    live_client: TestClient,
    staff_by_role: dict[StaffRole, Staff],
    method: str,
    path: str,
    role: StaffRole,
) -> None:
    # Without this half the table could list a role the code refuses and still pass.
    response = live_client.request(method, _url(path), headers=staff_by_role[role].headers)

    _assert_past_the_guard(response)


@pytest.mark.integration
@pytest.mark.parametrize(("method", "path"), CLIENT_CASES)
def test_a_client_route_lets_a_client_through(
    live_client: TestClient, signed_in_client: dict[str, str], method: str, path: str
) -> None:
    response = live_client.request(method, _url(path), headers=signed_in_client)

    _assert_past_the_guard(response)


def _receive(socket: WebSocketTestSession, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    # The test client has no receive timeout: a missing message would hang the whole run. A daemon
    # thread that nobody joins, because a pool would wait on leaving for that very message.
    received: Future[dict[str, Any]] = Future()

    def read() -> None:
        try:
            received.set_result(socket.receive_json())
        except Exception as error:
            received.set_exception(error)

    threading.Thread(target=read, daemon=True).start()
    return received.result(timeout)


def test_a_silent_stream_fails_the_test_instead_of_hanging_the_run() -> None:
    app = FastAPI()

    @app.websocket("/silent")
    async def silent(socket: WebSocket) -> None:
        await socket.accept()
        await anyio.sleep_forever()

    with (
        TestClient(app) as client,
        client.websocket_connect("/silent") as socket,
        pytest.raises(TimeoutError),
    ):
        _receive(socket, timeout=0.1)


def _read_until_closed(socket: WebSocketTestSession) -> None:
    while True:
        assert _receive(socket)["type"] != "snapshot", "the stream let the caller in"


def _stream_close_code(client: TestClient, first_message: dict[str, str]) -> int:
    with client.websocket_connect(STREAM_ROUTE[1], headers={"origin": ORIGIN}) as socket:
        socket.send_json(first_message)
        with pytest.raises(WebSocketDisconnect) as closed:
            _read_until_closed(socket)
    return closed.value.code


@pytest.mark.integration
def test_the_stream_refuses_anonymous_callers(live_client: TestClient) -> None:
    assert _stream_close_code(live_client, {"type": "auth"}) == stream.CLOSE_POLICY


@pytest.mark.integration
def test_the_stream_refuses_a_client_token(
    live_client: TestClient, live_settings: Settings
) -> None:
    message = {"type": "auth", "token": client_token(live_settings)}

    assert _stream_close_code(live_client, message) == stream.CLOSE_UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.parametrize("role", sorted(STREAM_ROLES))
def test_the_stream_lets_a_listed_role_in(
    live_client: TestClient, staff_by_role: dict[StaffRole, Staff], role: StaffRole
) -> None:
    with live_client.websocket_connect(STREAM_ROUTE[1], headers={"origin": ORIGIN}) as socket:
        socket.send_json({"type": "auth", "token": staff_by_role[role].token()})

        assert _receive(socket)["type"] == "snapshot"
