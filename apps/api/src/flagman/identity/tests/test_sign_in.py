import asyncio
import secrets
import uuid
from collections.abc import Callable, Iterator
from http import HTTPStatus
from typing import Annotated, Any

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Row, text

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine
from flagman.identity.api import get_sms_sender
from flagman.identity.dependencies import CurrentClient, get_current_client
from flagman.identity.domain import REFRESH_REUSE_GRACE, generate_otp_code, hash_refresh_token

pytestmark = pytest.mark.integration


class FakeSms:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def choose_code(self, phone: str) -> str:
        return generate_otp_code()

    async def send_otp(self, phone: str, code: str) -> None:
        self.sent.append((phone, code))

    def code_for(self, phone: str) -> str:
        return next(code for sent_to, code in reversed(self.sent) if sent_to == phone)


@pytest.fixture
def sms() -> FakeSms:
    return FakeSms()


@pytest.fixture
def reset_limits(integration_settings: Settings) -> Callable[[], None]:
    """Clears every rate limit counter; the Redis container is shared by the test session."""

    def reset() -> None:
        with Redis(
            host=integration_settings.redis_host,
            port=integration_settings.redis_port,
            password=integration_settings.redis_password.get_secret_value(),
        ) as redis:
            redis.flushdb()

    return reset


@pytest.fixture
def client(
    integration_settings: Settings, sms: FakeSms, reset_limits: Callable[[], None]
) -> Iterator[TestClient]:
    app = create_app(integration_settings)
    app.dependency_overrides[get_sms_sender] = lambda: sms
    _add_protected_route(app)
    reset_limits()
    # https: the web client's refresh cookie is Secure, and the cookie jar keeps it off plain http.
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _add_protected_route(app: FastAPI) -> None:
    @app.get("/test/me")
    async def me(client: Annotated[CurrentClient, Depends(get_current_client)]) -> dict[str, str]:
        return {"user_id": str(client.user_id), "session_id": str(client.session_id)}


@pytest.fixture
def phone() -> str:
    # A new phone per test: users and codes stay in the shared database between tests.
    return f"+37529{secrets.randbelow(10**7):07d}"


def _query(settings: Settings, sql: str, **params: Any) -> list[Row[Any]]:
    async def run() -> list[Row[Any]]:
        engine = create_engine(settings)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _request_code(client: TestClient, phone: str) -> None:
    response = client.post("/auth/otp/request", json={"phone": phone})
    assert response.status_code == HTTPStatus.ACCEPTED, response.text


def _verify(
    client: TestClient,
    phone: str,
    code: str,
    device_id: uuid.UUID | None = None,
    language: str | None = None,
) -> Any:
    body = {"phone": phone, "code": code, "device_id": str(device_id or uuid.uuid4())}
    if language is not None:
        body["language"] = language
    return client.post("/auth/otp/verify", json=body)


def _sign_in(
    client: TestClient,
    sms: FakeSms,
    phone: str,
    device_id: uuid.UUID | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    _request_code(client, phone)
    response = _verify(client, phone, sms.code_for(phone), device_id, language)
    assert response.status_code == HTTPStatus.OK, response.text
    tokens: dict[str, Any] = response.json()
    return tokens


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def _refresh(client: TestClient, refresh_token: str) -> Any:
    return client.post("/auth/token/refresh", json={"refresh_token": refresh_token})


def _end_reuse_grace(settings: Settings, refresh_token: str) -> None:
    """Moves the rotation of this token back past the grace window, as if time had passed."""
    _query(
        settings,
        "UPDATE refresh_tokens SET used_at = used_at - CAST(:age AS interval)"
        " WHERE token_hash = :token_hash",
        age=REFRESH_REUSE_GRACE,
        token_hash=hash_refresh_token(refresh_token),
    )


def _me(client: TestClient, access_token: str) -> Any:
    return client.get("/test/me", headers={"Authorization": f"Bearer {access_token}"})


def test_sign_in_issues_tokens_that_open_protected_routes(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    response = client.post("/auth/otp/request", json={"phone": phone})

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json() == {"expires_in": 300, "resend_after": 60}

    tokens = _sign_in_with_sent_code(client, sms, phone)

    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 900
    me = _me(client, tokens["access_token"])
    assert me.status_code == HTTPStatus.OK
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    assert me.json() == {"user_id": claims["sub"], "session_id": claims["sid"]}


def _sign_in_with_sent_code(client: TestClient, sms: FakeSms, phone: str) -> dict[str, Any]:
    response = _verify(client, phone, sms.code_for(phone))
    assert response.status_code == HTTPStatus.OK, response.text
    tokens: dict[str, Any] = response.json()
    return tokens


def test_the_same_phone_is_the_same_user(
    client: TestClient, sms: FakeSms, phone: str, reset_limits: Callable[[], None]
) -> None:
    first = _sign_in(client, sms, phone)
    reset_limits()  # the second code would hit the 60 s resend limit
    second = _sign_in(client, sms, phone)

    first_user = _me(client, first["access_token"]).json()["user_id"]
    assert _me(client, second["access_token"]).json()["user_id"] == first_user


def test_only_hashes_are_stored(
    client: TestClient, sms: FakeSms, phone: str, integration_settings: Settings
) -> None:
    _request_code(client, phone)
    code = sms.code_for(phone)

    [(code_hash,)] = _query(
        integration_settings, "SELECT code_hash FROM pending_otps WHERE phone = :phone", phone=phone
    )
    assert code not in code_hash

    tokens = _sign_in_with_sent_code(client, sms, phone)

    stored = _query(
        integration_settings,
        "SELECT 1 FROM refresh_tokens WHERE token_hash = :token",
        token=tokens["refresh_token"],
    )
    assert stored == []


def test_wrong_code_can_be_retried(client: TestClient, sms: FakeSms, phone: str) -> None:
    _request_code(client, phone)
    response = _verify(client, phone, _wrong(sms.code_for(phone)))

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["type"] == "urn:flagman:problem:otp-invalid"
    assert _verify(client, phone, sms.code_for(phone)).status_code == HTTPStatus.OK


def test_code_is_burnt_after_five_wrong_attempts(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    _request_code(client, phone)
    code = sms.code_for(phone)

    types = [_verify(client, phone, _wrong(code)).json()["type"] for _ in range(5)]

    assert types == ["urn:flagman:problem:otp-invalid"] * 4 + ["urn:flagman:problem:otp-expired"]
    response = _verify(client, phone, code)
    assert response.json()["type"] == "urn:flagman:problem:otp-expired"


def test_code_works_only_once(client: TestClient, sms: FakeSms, phone: str) -> None:
    _sign_in(client, sms, phone)

    response = _verify(client, phone, sms.code_for(phone))

    assert response.json()["type"] == "urn:flagman:problem:otp-expired"


def test_expired_code_is_rejected(
    client: TestClient, sms: FakeSms, phone: str, integration_settings: Settings
) -> None:
    _request_code(client, phone)
    _query(
        integration_settings,
        "UPDATE pending_otps SET expires_at = now() - interval '1 second' WHERE phone = :phone",
        phone=phone,
    )

    response = _verify(client, phone, sms.code_for(phone))

    assert response.json()["type"] == "urn:flagman:problem:otp-expired"


def test_verify_without_a_requested_code_looks_like_an_expired_one(
    client: TestClient, phone: str
) -> None:
    response = _verify(client, phone, "123456")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["type"] == "urn:flagman:problem:otp-expired"


def test_new_code_replaces_the_previous_one(
    client: TestClient, sms: FakeSms, phone: str, reset_limits: Callable[[], None]
) -> None:
    _request_code(client, phone)
    old_code = sms.code_for(phone)
    reset_limits()
    _request_code(client, phone)
    new_code = sms.code_for(phone)

    if old_code != new_code:
        assert _verify(client, phone, old_code).status_code == HTTPStatus.BAD_REQUEST
    assert _verify(client, phone, new_code).status_code == HTTPStatus.OK


def test_code_can_be_resent_only_after_a_minute(client: TestClient, phone: str) -> None:
    _request_code(client, phone)

    response = client.post("/auth/otp/request", json={"phone": phone})

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.json()["type"] == "urn:flagman:problem:rate-limited"
    assert 0 < int(response.headers["retry-after"]) <= 60


def test_codes_per_phone_are_limited_per_hour(
    client: TestClient, phone: str, integration_settings: Settings
) -> None:
    for _ in range(5):
        _request_code(client, phone)
        _skip_resend_cooldown(integration_settings)

    response = client.post("/auth/otp/request", json={"phone": phone})

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def _skip_resend_cooldown(settings: Settings) -> None:
    with Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
    ) as redis:
        keys = list(redis.scan_iter("rl:otp-request-phone-cooldown:*"))
        if keys:
            redis.delete(*keys)


def test_codes_per_ip_are_limited_per_hour(client: TestClient) -> None:
    for number in range(20):
        _request_code(client, f"+37533{number:07d}")

    response = client.post("/auth/otp/request", json={"phone": "+375339999999"})

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_guesses_per_phone_are_limited_across_codes(
    client: TestClient, sms: FakeSms, phone: str, integration_settings: Settings
) -> None:
    for _ in range(2):
        _request_code(client, phone)
        _skip_resend_cooldown(integration_settings)
        for _ in range(5):
            _verify(client, phone, _wrong(sms.code_for(phone)))
    _request_code(client, phone)

    response = _verify(client, phone, sms.code_for(phone))

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_guesses_without_a_pending_code_do_not_lock_the_phone(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    for _ in range(11):
        assert _verify(client, phone, "123456").status_code == HTTPStatus.BAD_REQUEST

    _sign_in(client, sms, phone)


def test_guesses_per_ip_are_limited_per_hour(client: TestClient) -> None:
    for number in range(50):
        _verify(client, f"+37544{number:07d}", "123456")

    response = _verify(client, "+375449999999", "123456")

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_refresh_rotates_the_token(client: TestClient, sms: FakeSms, phone: str) -> None:
    tokens = _sign_in(client, sms, phone)

    response = _refresh(client, tokens["refresh_token"])

    assert response.status_code == HTTPStatus.OK
    rotated = response.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert _me(client, rotated["access_token"]).status_code == HTTPStatus.OK
    assert _refresh(client, rotated["refresh_token"]).status_code == HTTPStatus.OK


def test_reused_refresh_token_revokes_the_device_sessions(
    client: TestClient, sms: FakeSms, phone: str, integration_settings: Settings
) -> None:
    tokens = _sign_in(client, sms, phone)
    rotated = _refresh(client, tokens["refresh_token"]).json()
    _end_reuse_grace(integration_settings, tokens["refresh_token"])

    reuse = _refresh(client, tokens["refresh_token"])

    assert reuse.status_code == HTTPStatus.UNAUTHORIZED
    assert reuse.json()["type"] == "urn:flagman:problem:refresh-token-invalid"
    # The legitimate-looking newest token dies too: we cannot tell who holds which copy.
    assert _refresh(client, rotated["refresh_token"]).status_code == HTTPStatus.UNAUTHORIZED


def test_token_rotated_moments_ago_is_turned_down_without_revoking(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    tokens = _sign_in(client, sms, phone)
    rotated = _refresh(client, tokens["refresh_token"]).json()

    late = _refresh(client, tokens["refresh_token"])

    assert late.status_code == HTTPStatus.UNAUTHORIZED
    assert late.json()["type"] == "urn:flagman:problem:refresh-token-invalid"
    # The request that lost the race retries with the newer token, and the session goes on.
    assert _me(client, rotated["access_token"]).status_code == HTTPStatus.OK
    assert _refresh(client, rotated["refresh_token"]).status_code == HTTPStatus.OK


def test_reuse_on_one_device_keeps_other_devices_signed_in(
    client: TestClient,
    sms: FakeSms,
    phone: str,
    reset_limits: Callable[[], None],
    integration_settings: Settings,
) -> None:
    phone_tokens = _sign_in(client, sms, phone, device_id=uuid.uuid4())
    reset_limits()
    tablet_tokens = _sign_in(client, sms, phone, device_id=uuid.uuid4())
    phone_rotated = _refresh(client, phone_tokens["refresh_token"]).json()
    _end_reuse_grace(integration_settings, phone_tokens["refresh_token"])

    _refresh(client, phone_tokens["refresh_token"])

    assert _refresh(client, phone_rotated["refresh_token"]).status_code == HTTPStatus.UNAUTHORIZED
    assert _refresh(client, tablet_tokens["refresh_token"]).status_code == HTTPStatus.OK


def test_new_sign_in_on_the_same_device_ends_the_old_session(
    client: TestClient, sms: FakeSms, phone: str, reset_limits: Callable[[], None]
) -> None:
    device_id = uuid.uuid4()
    old = _sign_in(client, sms, phone, device_id=device_id)
    reset_limits()
    new = _sign_in(client, sms, phone, device_id=device_id)

    assert _refresh(client, old["refresh_token"]).status_code == HTTPStatus.UNAUTHORIZED
    assert _refresh(client, new["refresh_token"]).status_code == HTTPStatus.OK


def _sign_in_again_after_refresh(
    client: TestClient, sms: FakeSms, phone: str, reset_limits: Callable[[], None]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Issue #82: the old session's first token is used, then a new session replaces it."""
    device_id = uuid.uuid4()
    old = _sign_in(client, sms, phone, device_id=device_id)
    _refresh(client, old["refresh_token"])
    reset_limits()
    new = _sign_in(client, sms, phone, device_id=device_id)
    return old, new


def _assert_replay_leaves_the_new_session_alone(
    client: TestClient, old: dict[str, Any], new: dict[str, Any]
) -> None:
    # Repeated on purpose: each replay used to sign the owner out of the device again.
    for _ in range(2):
        replay = _refresh(client, old["refresh_token"])
        assert replay.status_code == HTTPStatus.UNAUTHORIZED
        assert replay.json()["type"] == "urn:flagman:problem:refresh-token-invalid"

    assert _refresh(client, new["refresh_token"]).status_code == HTTPStatus.OK


def test_replay_from_a_revoked_session_leaves_the_new_session_alone(
    client: TestClient, sms: FakeSms, phone: str, reset_limits: Callable[[], None]
) -> None:
    old, new = _sign_in_again_after_refresh(client, sms, phone, reset_limits)

    _assert_replay_leaves_the_new_session_alone(client, old, new)


def test_late_replay_from_a_revoked_session_leaves_the_new_session_alone(
    client: TestClient,
    sms: FakeSms,
    phone: str,
    reset_limits: Callable[[], None],
    integration_settings: Settings,
) -> None:
    old, new = _sign_in_again_after_refresh(client, sms, phone, reset_limits)
    _end_reuse_grace(integration_settings, old["refresh_token"])

    _assert_replay_leaves_the_new_session_alone(client, old, new)


def test_replay_from_an_expired_session_revokes_nothing(
    client: TestClient,
    sms: FakeSms,
    phone: str,
    integration_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tokens = _sign_in(client, sms, phone)
    _refresh(client, tokens["refresh_token"])
    _end_reuse_grace(integration_settings, tokens["refresh_token"])
    token_hash = hash_refresh_token(tokens["refresh_token"])
    _query(
        integration_settings,
        "UPDATE auth_sessions SET expires_at = now() - interval '1 second'"
        " WHERE id = (SELECT session_id FROM refresh_tokens WHERE token_hash = :token_hash)",
        token_hash=token_hash,
    )

    replay = _refresh(client, tokens["refresh_token"])

    assert replay.status_code == HTTPStatus.UNAUTHORIZED
    assert replay.json()["type"] == "urn:flagman:problem:refresh-token-invalid"
    revoked = _query(
        integration_settings,
        "SELECT s.revoked_at FROM auth_sessions s"
        " JOIN refresh_tokens t ON t.session_id = s.id WHERE t.token_hash = :token_hash",
        token_hash=token_hash,
    )
    assert revoked[0].revoked_at is None
    assert "refresh_token_replayed_after_session_end" in caplog.text
    assert "refresh_token_reuse_detected" not in caplog.text


def _language(client: TestClient, tokens: dict[str, Any]) -> str:
    response = client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert response.status_code == HTTPStatus.OK, response.text
    language: str = response.json()["language"]
    return language


@pytest.mark.parametrize(("sent", "stored"), [("en", "en"), ("ru", "ru"), (None, "ru")])
def test_new_client_gets_the_language_of_the_device(
    client: TestClient, sms: FakeSms, phone: str, sent: str | None, stored: str
) -> None:
    tokens = _sign_in(client, sms, phone, language=sent)

    assert _language(client, tokens) == stored


def test_sign_in_keeps_the_language_the_client_chose(
    client: TestClient, sms: FakeSms, phone: str, reset_limits: Callable[[], None]
) -> None:
    tokens = _sign_in(client, sms, phone, language="en")
    chosen = client.patch(
        "/me",
        json={"language": "ru"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert chosen.status_code == HTTPStatus.OK, chosen.text
    reset_limits()

    # A phone with English settings must not undo the choice made in the app.
    again = _sign_in(client, sms, phone, language="en")

    assert _language(client, again) == "ru"


def test_unknown_device_language_is_rejected(client: TestClient, sms: FakeSms, phone: str) -> None:
    _request_code(client, phone)

    response = _verify(client, phone, sms.code_for(phone), language="de")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    # Rejected by the schema before the code is checked: the code is still pending.
    assert _verify(client, phone, sms.code_for(phone)).status_code == HTTPStatus.OK


def test_unknown_refresh_token_is_rejected(client: TestClient) -> None:
    response = _refresh(client, secrets.token_urlsafe(32))

    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_logout_ends_the_session_and_is_idempotent(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    tokens = _sign_in(client, sms, phone)
    body = {"refresh_token": tokens["refresh_token"]}

    assert client.post("/auth/logout", json=body).status_code == HTTPStatus.NO_CONTENT
    assert client.post("/auth/logout", json=body).status_code == HTTPStatus.NO_CONTENT
    assert _refresh(client, tokens["refresh_token"]).status_code == HTTPStatus.UNAUTHORIZED
    unknown = {"refresh_token": secrets.token_urlsafe(32)}
    assert client.post("/auth/logout", json=unknown).status_code == HTTPStatus.NO_CONTENT


def _web_sign_in(client: TestClient, sms: FakeSms, phone: str) -> Any:
    _request_code(client, phone)
    body = {
        "phone": phone,
        "code": sms.code_for(phone),
        "device_id": str(uuid.uuid4()),
        "refresh_token_transport": "cookie",
    }
    return client.post("/auth/otp/verify", json=body)


def test_web_client_gets_the_refresh_token_only_in_a_cookie(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    response = _web_sign_in(client, sms, phone)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["refresh_token"] is None
    assert _me(client, response.json()["access_token"]).status_code == HTTPStatus.OK
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("flagman_refresh=")
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/auth", "Max-Age=2592000"):
        assert attribute in cookie


def test_web_client_refreshes_and_signs_out_with_the_cookie(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    _web_sign_in(client, sms, phone)
    first = client.cookies["flagman_refresh"]

    refreshed = client.post("/auth/token/refresh", json={})

    assert refreshed.status_code == HTTPStatus.OK
    assert refreshed.json()["refresh_token"] is None
    assert client.cookies["flagman_refresh"] != first
    assert client.post("/auth/logout", json={}).status_code == HTTPStatus.NO_CONTENT
    assert "flagman_refresh" not in client.cookies
    assert client.post("/auth/token/refresh", json={}).status_code == HTTPStatus.UNAUTHORIZED


def test_reused_refresh_cookie_revokes_the_device_sessions(
    client: TestClient, sms: FakeSms, phone: str, integration_settings: Settings
) -> None:
    _web_sign_in(client, sms, phone)
    stolen = client.cookies["flagman_refresh"]
    client.post("/auth/token/refresh", json={})
    _end_reuse_grace(integration_settings, stolen)

    assert _refresh(client, stolen).status_code == HTTPStatus.UNAUTHORIZED
    assert client.post("/auth/token/refresh", json={}).status_code == HTTPStatus.UNAUTHORIZED


def test_second_browser_tab_with_the_old_cookie_does_not_sign_the_user_out(
    client: TestClient, sms: FakeSms, phone: str
) -> None:
    _web_sign_in(client, sms, phone)
    old_cookie = client.cookies["flagman_refresh"]
    # The first tab rotates; the second one had already sent the cookie it saw before.
    assert client.post("/auth/token/refresh", json={}).status_code == HTTPStatus.OK

    second_tab = client.post(
        "/auth/token/refresh", json={}, headers={"Cookie": f"flagman_refresh={old_cookie}"}
    )

    assert second_tab.status_code == HTTPStatus.UNAUTHORIZED
    # Its retry goes out with the cookie the first tab received, and the session is intact.
    assert client.post("/auth/token/refresh", json={}).status_code == HTTPStatus.OK


def test_refresh_without_any_token_is_rejected(client: TestClient) -> None:
    response = client.post("/auth/token/refresh", json={})

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["type"] == "urn:flagman:problem:refresh-token-invalid"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ],
)
def test_protected_route_needs_a_valid_access_token(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.get("/test/me", headers=headers)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["type"] == "urn:flagman:problem:access-token-invalid"


def test_refresh_token_is_not_an_access_token(client: TestClient, sms: FakeSms, phone: str) -> None:
    tokens = _sign_in(client, sms, phone)

    assert _me(client, tokens["refresh_token"]).status_code == HTTPStatus.UNAUTHORIZED


def test_phones_codes_and_tokens_stay_out_of_the_logs(
    client: TestClient,
    sms: FakeSms,
    phone: str,
    caplog: pytest.LogCaptureFixture,
    integration_settings: Settings,
) -> None:
    tokens = _sign_in(client, sms, phone)
    rotated = _refresh(client, tokens["refresh_token"]).json()
    _refresh(client, tokens["refresh_token"])  # within the grace window
    _end_reuse_grace(integration_settings, tokens["refresh_token"])
    _refresh(client, tokens["refresh_token"])  # reuse: logged as a security event

    logs = caplog.text

    assert "refresh_token_already_rotated" in logs
    assert "refresh_token_reuse_detected" in logs
    for secret in (
        phone,
        phone[1:],
        sms.code_for(phone),
        tokens["access_token"],
        tokens["refresh_token"],
        rotated["refresh_token"],
    ):
        assert secret not in logs
