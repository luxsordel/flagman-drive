import asyncio
import io
import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Row, text

from flagman.core.app import create_app
from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import cli, service
from flagman.identity.domain import (
    REFRESH_REUSE_GRACE,
    TOTP_INTERVAL_SECONDS,
    StaffRole,
    hash_refresh_token,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
COOKIE = "flagman_staff_refresh"


@dataclass(frozen=True)
class Staff:
    id: uuid.UUID
    email: str
    password: str = PASSWORD


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


def _create_staff(settings: Settings, email: str, role: StaffRole) -> uuid.UUID:
    async def run() -> uuid.UUID:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await service.create_staff(db, email, PASSWORD, role, datetime.now(UTC))
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
def reset_limits(integration_settings: Settings) -> Callable[[], None]:
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
    integration_settings: Settings, reset_limits: Callable[[], None]
) -> Iterator[TestClient]:
    reset_limits()
    # https: the refresh cookie is Secure, and the cookie jar keeps it off plain http.
    with TestClient(create_app(integration_settings), base_url="https://testserver") as client:
        yield client


@pytest.fixture
def make_staff(integration_settings: Settings) -> Callable[[StaffRole], Staff]:
    def make(role: StaffRole = StaffRole.OPERATOR) -> Staff:
        # A new email per staff member: accounts stay in the shared database between tests.
        email = f"staff-{secrets.token_hex(6)}@flagman.by"
        return Staff(_create_staff(integration_settings, email, role), email)

    return make


@pytest.fixture
def operator(make_staff: Callable[[StaffRole], Staff]) -> Staff:
    return make_staff(StaffRole.OPERATOR)


def _password(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return client.post("/staff/auth/password", json={"email": email, "password": password})


def _challenge(client: TestClient, staff: Staff) -> dict[str, Any]:
    response = _password(client, staff.email, staff.password)
    assert response.status_code == HTTPStatus.OK, response.text
    body: dict[str, Any] = response.json()
    return body


def _enroll(client: TestClient, challenge_token: str) -> str:
    response = client.post("/staff/auth/totp/enrollment", json={"challenge_token": challenge_token})
    assert response.status_code == HTTPStatus.OK, response.text
    secret: str = response.json()["secret"]
    return secret


def _code(secret: str, steps_ahead: int = 0) -> str:
    # Steps ahead stay inside the accepted window: a later sign-in in the same test needs a code
    # of a newer step than the last accepted one.
    at = datetime.now(UTC) + timedelta(seconds=steps_ahead * TOTP_INTERVAL_SECONDS)
    return pyotp.TOTP(secret).at(at)


def _totp(client: TestClient, challenge_token: str, code: str) -> Any:
    return client.post("/staff/auth/totp", json={"challenge_token": challenge_token, "code": code})


def _first_sign_in(client: TestClient, staff: Staff) -> tuple[str, dict[str, Any]]:
    """Signs in with 2FA setup; returns the TOTP secret and the access token response."""
    challenge = _challenge(client, staff)["challenge_token"]
    secret = _enroll(client, challenge)
    response = _totp(client, challenge, _code(secret))
    assert response.status_code == HTTPStatus.OK, response.text
    body: dict[str, Any] = response.json()
    return secret, body


def _me(client: TestClient, access_token: str) -> Any:
    return client.get("/staff/me", headers={"Authorization": f"Bearer {access_token}"})


def _refresh(client: TestClient, cookie: str | None = None) -> Any:
    headers = {"Cookie": f"{COOKIE}={cookie}"} if cookie is not None else None
    return client.post("/staff/auth/token/refresh", json={}, headers=headers)


def _events(settings: Settings, actor_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    rows = _query(
        settings,
        "SELECT action, details FROM audit_events WHERE actor_id = :actor_id"
        " ORDER BY occurred_at, id",
        actor_id=actor_id,
    )
    return [(row.action, row.details) for row in rows]


def _problem(response: Any) -> str:
    problem_type: str = response.json()["type"]
    return problem_type.removeprefix("urn:flagman:problem:")


# The first sign-in sets up 2FA


def test_first_sign_in_sets_up_2fa_and_opens_a_session(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    challenge = _challenge(client, operator)
    assert challenge["next_step"] == "totp_enrollment"
    assert challenge["expires_in"] == 300

    enrollment = client.post(
        "/staff/auth/totp/enrollment", json={"challenge_token": challenge["challenge_token"]}
    )
    assert enrollment.status_code == HTTPStatus.OK
    assert enrollment.headers["cache-control"] == "no-store"
    secret = enrollment.json()["secret"]
    assert f"secret={secret}" in enrollment.json()["otpauth_uri"]

    response = _totp(client, challenge["challenge_token"], _code(secret))

    assert response.status_code == HTTPStatus.OK, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["expires_in"] == 900
    me = _me(client, body["access_token"])
    assert me.status_code == HTTPStatus.OK
    assert me.json() == {"id": str(operator.id), "email": operator.email, "role": "operator"}
    actions = [action for action, _ in _events(integration_settings, operator.id)]
    assert actions == [
        "staff.totp_enrollment_started",
        "staff.totp_enrolled",
        "staff.signed_in",
    ]


def test_refresh_token_comes_only_in_a_strict_cookie(client: TestClient, operator: Staff) -> None:
    challenge = _challenge(client, operator)["challenge_token"]
    secret = _enroll(client, challenge)

    response = _totp(client, challenge, _code(secret))

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/staff/auth", "Max-Age=43200"):
        assert attribute in cookie
    assert client.cookies[COOKIE] not in response.text


def test_totp_secret_is_stored_encrypted(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    secret, _ = _first_sign_in(client, operator)

    [(stored,)] = _query(
        integration_settings,
        "SELECT totp_secret_encrypted FROM staff_members WHERE id = :id",
        id=operator.id,
    )
    assert secret.encode() not in bytes(stored)


def test_enrollment_cannot_replace_a_set_up_authenticator(
    client: TestClient, operator: Staff
) -> None:
    # Otherwise a stolen password alone would let the thief put in their own authenticator.
    _first_sign_in(client, operator)
    challenge = _challenge(client, operator)
    assert challenge["next_step"] == "totp"

    response = client.post(
        "/staff/auth/totp/enrollment", json={"challenge_token": challenge["challenge_token"]}
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert _problem(response) == "totp-already-enrolled"


def test_code_before_the_qr_code_is_refused(client: TestClient, operator: Staff) -> None:
    challenge = _challenge(client, operator)["challenge_token"]

    response = _totp(client, challenge, "123456")

    assert response.status_code == HTTPStatus.CONFLICT
    assert _problem(response) == "totp-enrollment-not-started"


def test_unconfirmed_setup_can_be_restarted(client: TestClient, operator: Staff) -> None:
    challenge = _challenge(client, operator)["challenge_token"]
    abandoned = _enroll(client, challenge)
    current = _enroll(client, challenge)

    assert _totp(client, challenge, _code(abandoned)).status_code == HTTPStatus.BAD_REQUEST
    assert _totp(client, challenge, _code(current)).status_code == HTTPStatus.OK


# Later sign-ins


def test_later_sign_in_needs_a_fresh_code(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    first = _challenge(client, operator)["challenge_token"]
    secret = _enroll(client, first)
    used = _code(secret)
    assert _totp(client, first, used).status_code == HTTPStatus.OK
    challenge = _challenge(client, operator)["challenge_token"]

    # The very same code string: recomputing it could land on the next time step.
    replayed = _totp(client, challenge, used)
    fresh = _totp(client, challenge, _code(secret, steps_ahead=1))

    assert replayed.status_code == HTTPStatus.BAD_REQUEST
    assert _problem(replayed) == "totp-invalid"
    assert fresh.status_code == HTTPStatus.OK
    failed = [d for a, d in _events(integration_settings, operator.id) if a.endswith("failed")]
    assert failed == [{"step": "totp", "reason": "wrong_code"}]


def test_email_is_case_insensitive(client: TestClient, operator: Staff) -> None:
    response = _password(client, operator.email.upper())

    assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("email", ["İnna@flagman.by", "оператор@flagman.by"])
def test_email_must_be_ascii(client: TestClient, email: str) -> None:
    # Python and Postgres lowercase some non-ASCII letters differently.
    assert _password(client, email).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_qr_code_requests_count_against_the_totp_limit(client: TestClient, operator: Staff) -> None:
    challenge = _challenge(client, operator)["challenge_token"]
    for _ in range(10):
        _enroll(client, challenge)

    response = client.post("/staff/auth/totp/enrollment", json={"challenge_token": challenge})

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_wrong_password_unknown_email_and_disabled_account_look_the_same(
    client: TestClient,
    operator: Staff,
    make_staff: Callable[[StaffRole], Staff],
    integration_settings: Settings,
) -> None:
    disabled = make_staff(StaffRole.OPERATOR)
    _query(
        integration_settings,
        "UPDATE staff_members SET disabled_at = now() WHERE id = :id",
        id=disabled.id,
    )

    responses = [
        _password(client, operator.email, "wrong password, long enough"),
        _password(client, f"nobody-{secrets.token_hex(6)}@flagman.by"),
        _password(client, disabled.email),
    ]

    for response in responses:
        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert response.json()["title"] == "Invalid email or password"
        assert _problem(response) == "staff-credentials-invalid"
    assert _events(integration_settings, operator.id) == [
        ("staff.sign_in_failed", {"step": "password", "reason": "wrong_password"})
    ]
    assert _events(integration_settings, disabled.id) == [
        ("staff.sign_in_failed", {"step": "password", "reason": "disabled"})
    ]


def test_unknown_email_attempt_is_audited_without_the_email(
    client: TestClient, integration_settings: Settings
) -> None:
    request_id = str(uuid.uuid4())
    email = f"nobody-{secrets.token_hex(6)}@flagman.by"

    client.post(
        "/staff/auth/password",
        json={"email": email, "password": PASSWORD},
        headers={"X-Request-ID": request_id},
    )

    [event] = _query(
        integration_settings,
        "SELECT actor_type, action, details::text AS details FROM audit_events"
        " WHERE request_id = :request_id",
        request_id=request_id,
    )
    assert (event.actor_type, event.action) == ("system", "staff.sign_in_failed")
    assert "unknown_email" in event.details
    assert email not in event.details


def test_password_guessing_is_rate_limited(client: TestClient, operator: Staff) -> None:
    for _ in range(10):
        _password(client, operator.email, "wrong password, long enough")

    response = _password(client, operator.email)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert int(response.headers["retry-after"]) > 0


def test_totp_guessing_is_rate_limited(client: TestClient, operator: Staff) -> None:
    secret, _ = _first_sign_in(client, operator)
    challenge = _challenge(client, operator)["challenge_token"]
    wrong = "000000" if _code(secret, 1) != "000000" else "111111"
    # The first sign-in already counted the QR code request and one code.
    for _ in range(8):
        _totp(client, challenge, wrong)

    response = _totp(client, challenge, _code(secret, steps_ahead=1))

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.parametrize("token", ["garbage", "a" * 2048])
def test_forged_challenge_is_refused(client: TestClient, token: str) -> None:
    response = _totp(client, token, "123456")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "staff-challenge-invalid"


def test_access_token_is_not_a_challenge(client: TestClient, operator: Staff) -> None:
    _, tokens = _first_sign_in(client, operator)

    response = _totp(client, tokens["access_token"], "123456")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "staff-challenge-invalid"


def test_secret_that_does_not_decrypt_needs_a_reset(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    secret, _ = _first_sign_in(client, operator)
    # As after a change of API_TOTP_ENCRYPTION_KEY.
    _query(
        integration_settings,
        "UPDATE staff_members SET totp_secret_encrypted = :garbage WHERE id = :id",
        garbage=secrets.token_bytes(48),
        id=operator.id,
    )
    challenge = _challenge(client, operator)["challenge_token"]

    response = _totp(client, challenge, _code(secret, steps_ahead=1))

    assert response.status_code == HTTPStatus.CONFLICT
    assert _problem(response) == "totp-reset-required"


# Sessions


def test_refresh_rotates_the_cookie_and_logout_ends_the_session(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    first = client.cookies[COOKIE]

    refreshed = _refresh(client)

    assert refreshed.status_code == HTTPStatus.OK, refreshed.text
    assert _me(client, refreshed.json()["access_token"]).status_code == HTTPStatus.OK
    assert client.cookies[COOKIE] != first

    current = client.cookies[COOKIE]
    logout = client.post("/staff/auth/logout", json={})

    assert logout.status_code == HTTPStatus.NO_CONTENT
    assert COOKIE not in client.cookies
    assert _refresh(client, current).status_code == HTTPStatus.UNAUTHORIZED
    assert _events(integration_settings, operator.id)[-1][0] == "staff.signed_out"


def test_refresh_without_the_cookie_is_refused(client: TestClient) -> None:
    response = _refresh(client)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "refresh-token-invalid"


def test_reused_refresh_cookie_ends_the_session(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    stolen = client.cookies[COOKIE]
    assert _refresh(client).status_code == HTTPStatus.OK
    _query(
        integration_settings,
        "UPDATE staff_refresh_tokens SET used_at = used_at - CAST(:age AS interval)"
        " WHERE token_hash = :token_hash",
        age=REFRESH_REUSE_GRACE,
        token_hash=hash_refresh_token(stolen),
    )

    assert _refresh(client, stolen).status_code == HTTPStatus.UNAUTHORIZED
    # The owner's newer cookie belongs to the same session, which is gone now.
    assert _refresh(client).status_code == HTTPStatus.UNAUTHORIZED


def _revoked_at(settings: Settings, refresh_token: str) -> datetime | None:
    rows = _query(
        settings,
        "SELECT s.revoked_at FROM staff_sessions s"
        " JOIN staff_refresh_tokens t ON t.session_id = s.id WHERE t.token_hash = :token_hash",
        token_hash=hash_refresh_token(refresh_token),
    )
    revoked_at: datetime | None = rows[0].revoked_at
    return revoked_at


def test_replay_after_logout_is_turned_down_without_revoking(
    client: TestClient,
    operator: Staff,
    integration_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _first_sign_in(client, operator)
    stolen = client.cookies[COOKIE]
    assert _refresh(client).status_code == HTTPStatus.OK
    assert client.post("/staff/auth/logout", json={}).status_code == HTTPStatus.NO_CONTENT
    signed_out_at = _revoked_at(integration_settings, stolen)

    replay = _refresh(client, stolen)

    assert replay.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(replay) == "refresh-token-invalid"
    assert _revoked_at(integration_settings, stolen) == signed_out_at
    assert "staff_refresh_token_replayed_after_session_end" in caplog.text
    assert "staff_refresh_token_reuse_detected" not in caplog.text


def test_refresh_brings_the_current_role(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    _query(
        integration_settings,
        "UPDATE staff_members SET role = 'admin' WHERE id = :id",
        id=operator.id,
    )

    refreshed = _refresh(client)

    claims = jwt.decode(refreshed.json()["access_token"], options={"verify_signature": False})
    assert claims["role"] == "admin"


def _session_end(settings: Settings, staff_id: uuid.UUID) -> datetime:
    [(expires_at,)] = _query(
        settings, "SELECT expires_at FROM staff_sessions WHERE staff_id = :id", id=staff_id
    )
    end: datetime = expires_at
    return end


def test_refresh_does_not_extend_the_session(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    end = _session_end(integration_settings, operator.id)

    refreshed = _refresh(client)

    assert refreshed.status_code == HTTPStatus.OK
    assert _session_end(integration_settings, operator.id) == end
    # The new cookie expires with the session, not a full 12 hours after the refresh.
    max_age = int(refreshed.headers["set-cookie"].split("Max-Age=")[1].split(";")[0])
    assert max_age <= (end - datetime.now(UTC)).total_seconds() + 1


def test_session_ends_12_hours_after_sign_in_however_it_is_used(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    assert _refresh(client).status_code == HTTPStatus.OK
    # As if the tab had kept refreshing every 15 minutes since a sign-in 12 hours ago.
    _query(
        integration_settings,
        "UPDATE staff_sessions SET created_at = created_at - interval '12 hours',"
        " expires_at = expires_at - interval '12 hours' WHERE staff_id = :id",
        id=operator.id,
    )

    response = _refresh(client)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "refresh-token-invalid"


def test_challenge_opens_only_one_session(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    challenge = _challenge(client, operator)["challenge_token"]
    secret = _enroll(client, challenge)
    assert _totp(client, challenge, _code(secret)).status_code == HTTPStatus.OK

    # The next code is valid, but the password step has to be passed again.
    response = _totp(client, challenge, _code(secret, steps_ahead=1))

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "staff-challenge-invalid"
    # The refusal is committed to the audit log, not rolled back with the transaction.
    assert _events(integration_settings, operator.id)[-1] == (
        "staff.sign_in_failed",
        {"step": "totp", "reason": "challenge_used"},
    )
    fresh = _challenge(client, operator)["challenge_token"]
    assert _totp(client, fresh, _code(secret, steps_ahead=1)).status_code == HTTPStatus.OK


def test_disabled_account_cannot_refresh(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    _query(
        integration_settings,
        "UPDATE staff_members SET disabled_at = now() WHERE id = :id",
        id=operator.id,
    )

    assert _refresh(client).status_code == HTTPStatus.UNAUTHORIZED


def test_totp_reset_ends_sessions_and_asks_for_setup_again(
    client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)

    async def reset() -> None:
        engine = create_engine(integration_settings)
        try:
            async with create_session_factory(engine)() as db:
                await service.reset_staff_totp(db, operator.email, datetime.now(UTC))
        finally:
            await engine.dispose()

    asyncio.run(reset())

    assert _refresh(client).status_code == HTTPStatus.UNAUTHORIZED
    assert _challenge(client, operator)["next_step"] == "totp_enrollment"


# Roles


def test_admin_reads_the_audit_log_and_an_operator_does_not(
    client: TestClient, make_staff: Callable[[StaffRole], Staff]
) -> None:
    _, admin = _first_sign_in(client, make_staff(StaffRole.ADMIN))
    _, operator = _first_sign_in(client, make_staff(StaffRole.OPERATOR))

    def audit_log(access_token: str) -> Any:
        return client.get(
            "/admin/audit-events", headers={"Authorization": f"Bearer {access_token}"}
        )

    assert audit_log(admin["access_token"]).status_code == HTTPStatus.OK
    assert audit_log(operator["access_token"]).status_code == HTTPStatus.FORBIDDEN


def test_staff_routes_refuse_a_missing_token(client: TestClient) -> None:
    response = client.get("/staff/me")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "access-token-invalid"


# The command


@pytest.fixture
def command_env(
    integration_env: dict[str, str], migrated_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command reads the settings from the environment, like the API process does.
    for name, value in integration_env.items():
        monkeypatch.setenv(name, value)


def _run_command(*argv: str, stdin: str = "") -> tuple[int, str]:
    stdout = io.StringIO()
    code = cli.main(list(argv), stdin=io.StringIO(stdin), stdout=stdout)
    return code, stdout.getvalue()


def test_command_creates_the_first_admin_who_can_sign_in(
    command_env: None, client: TestClient, integration_settings: Settings
) -> None:
    email = f"admin-{secrets.token_hex(6)}@flagman.by"

    code, output = _run_command(
        "create", "--email", email, "--role", "admin", "--password-stdin", stdin=PASSWORD + "\n"
    )

    assert code == cli.EXIT_OK
    assert PASSWORD not in output
    [staff] = _query(
        integration_settings, "SELECT id, role FROM staff_members WHERE email = :e", e=email
    )
    assert staff.role == "admin"
    [(action,)] = _query(
        integration_settings,
        "SELECT action FROM audit_events WHERE target_id = :id AND actor_type = 'system'",
        id=staff.id,
    )
    assert action == "staff.created"
    _first_sign_in(client, Staff(staff.id, email))


def test_command_refuses_a_taken_email_and_a_short_password(
    command_env: None, operator: Staff
) -> None:
    taken, _ = _run_command(
        "create", "--email", operator.email, "--role", "admin", "--password-stdin", stdin=PASSWORD
    )
    short, _ = _run_command(
        "create",
        "--email",
        f"new-{secrets.token_hex(6)}@flagman.by",
        "--role",
        "admin",
        "--password-stdin",
        stdin="short",
    )

    assert taken == cli.EXIT_FAILED
    assert short == cli.EXIT_FAILED


@pytest.mark.parametrize("email", ["admin", "admin@", "two words@flagman.by"])
def test_command_refuses_an_email_nobody_could_sign_in_with(email: str) -> None:
    # Checked before any database access, so no environment is needed.
    with pytest.raises(SystemExit) as exit_info:
        _run_command("create", "--email", email, "--role", "admin", "--password-stdin")

    assert exit_info.value.code == 2  # argparse usage error


def _system_events(settings: Settings, staff_id: uuid.UUID) -> list[str]:
    rows = _query(
        settings,
        "SELECT action FROM audit_events WHERE target_id = :id AND actor_type = 'system'"
        " ORDER BY occurred_at, id",
        id=staff_id,
    )
    return [row.action for row in rows]


def test_set_password_replaces_the_old_one_and_ends_sessions(
    command_env: None, client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)
    new_password = "a brand new passphrase"

    code, output = _run_command(
        "set-password", "--email", operator.email, "--password-stdin", stdin=new_password
    )

    assert code == cli.EXIT_OK
    assert new_password not in output
    assert "15 minutes" in output
    assert _refresh(client).status_code == HTTPStatus.UNAUTHORIZED
    assert _password(client, operator.email).status_code == HTTPStatus.UNAUTHORIZED
    assert _password(client, operator.email, new_password).status_code == HTTPStatus.OK
    assert _system_events(integration_settings, operator.id)[-1] == "staff.password_changed"


def test_set_password_applies_the_policy(command_env: None, operator: Staff) -> None:
    code, _ = _run_command(
        "set-password", "--email", operator.email, "--password-stdin", stdin="short"
    )

    assert code == cli.EXIT_FAILED


def test_disable_blocks_sign_in_and_ends_sessions(
    command_env: None, client: TestClient, operator: Staff, integration_settings: Settings
) -> None:
    _first_sign_in(client, operator)

    code, output = _run_command("disable", "--email", operator.email)

    assert code == cli.EXIT_OK
    assert "15 minutes" in output
    assert _refresh(client).status_code == HTTPStatus.UNAUTHORIZED
    response = _password(client, operator.email)
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert _problem(response) == "staff-credentials-invalid"
    assert _system_events(integration_settings, operator.id)[-1] == "staff.disabled"


def test_disabling_twice_changes_nothing(
    command_env: None, operator: Staff, integration_settings: Settings
) -> None:
    _run_command("disable", "--email", operator.email)

    code, output = _run_command("disable", "--email", operator.email)

    assert code == cli.EXIT_OK
    assert "Already disabled" in output
    assert _system_events(integration_settings, operator.id).count("staff.disabled") == 1


def test_reset_totp_warns_about_tokens_and_the_password(command_env: None, operator: Staff) -> None:
    code, output = _run_command("reset-totp", "--email", operator.email)

    assert code == cli.EXIT_OK
    assert "15 minutes" in output
    assert "set-password" in output


@pytest.mark.parametrize(
    "command", [["set-password", "--password-stdin"], ["reset-totp"], ["disable"]]
)
def test_commands_report_an_unknown_email(command_env: None, command: list[str]) -> None:
    email = f"nobody-{secrets.token_hex(6)}@flagman.by"

    code, _ = _run_command(*command, "--email", email, stdin=PASSWORD)

    assert code == cli.EXIT_FAILED


# The stand seed (#123)


def _ensure(
    email: str, secret: str, role: str = "operator", password: str = PASSWORD
) -> tuple[int, str]:
    stdin = f"{password}\n{secret}\n"
    return _run_command("ensure", "--email", email, "--role", role, stdin=stdin)


def _staff_by_email(settings: Settings, email: str) -> list[Row[Any]]:
    return _query(settings, "SELECT id, role FROM staff_members WHERE email = :e", e=email)


def test_ensure_creates_staff_who_sign_in_with_the_preset_2fa(
    command_env: None, client: TestClient, integration_settings: Settings
) -> None:
    email = f"stand-{secrets.token_hex(6)}@flagman.by"
    secret = pyotp.random_base32()

    code, output = _ensure(email, secret)

    assert code == cli.EXIT_OK
    assert PASSWORD not in output
    assert secret not in output
    [staff] = _staff_by_email(integration_settings, email)
    challenge = _challenge(client, Staff(staff.id, email))
    # No QR code step: whoever shows the stand already has the secret in an authenticator.
    assert challenge["next_step"] == "totp"
    response = _totp(client, challenge["challenge_token"], _code(secret))
    assert response.status_code == HTTPStatus.OK, response.text


def test_ensure_again_changes_nothing_and_keeps_sessions(
    command_env: None, client: TestClient, integration_settings: Settings
) -> None:
    email = f"stand-{secrets.token_hex(6)}@flagman.by"
    secret = pyotp.random_base32()
    assert _ensure(email, secret)[0] == cli.EXIT_OK
    [staff] = _staff_by_email(integration_settings, email)
    challenge = _challenge(client, Staff(staff.id, email))["challenge_token"]
    assert _totp(client, challenge, _code(secret)).status_code == HTTPStatus.OK

    code, output = _ensure(
        email, pyotp.random_base32(), role="admin", password="another passphrase"
    )

    assert code == cli.EXIT_OK
    assert "nothing changed" in output
    assert "operator" in output
    [after] = _staff_by_email(integration_settings, email)
    assert after.role == "operator"
    assert _refresh(client).status_code == HTTPStatus.OK
    assert _password(client, email).status_code == HTTPStatus.OK
    assert _system_events(integration_settings, staff.id) == ["staff.created"]


@pytest.mark.parametrize(
    ("password", "secret"),
    [("short", pyotp.random_base32()), (PASSWORD, "JBSWY3DPEHPK3PXP"), (PASSWORD, "")],
)
def test_ensure_refuses_a_weak_password_or_2fa_secret(
    command_env: None, integration_settings: Settings, password: str, secret: str
) -> None:
    email = f"stand-{secrets.token_hex(6)}@flagman.by"

    code, _ = _ensure(email, secret, password=password)

    assert code == cli.EXIT_FAILED
    assert _staff_by_email(integration_settings, email) == []
