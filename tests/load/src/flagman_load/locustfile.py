"""The scenarios of a run. Started by `flagman-load`, which prepares the state this file reads."""

import json
import os
import random
import time
import uuid
from collections import deque
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gevent
import websocket
from locust import HttpUser, User, between, constant, events, task
from locust.exception import StopUser
from requests import Response

from flagman_load.photos import make_photo
from flagman_load.pools import client_ip
from flagman_load.problems import error_code
from flagman_load.profiles import (
    MAP_CARS,
    MAP_ZONES,
    OTP_REQUEST,
    OTP_VERIFY,
    PROFILES,
    RESERVE,
    STREAM_SNAPSHOT,
    TELEMETRY_LAG,
)
from flagman_load.rental_flow import (
    MAP_CENTER,
    MAP_LIMIT,
    RentalCounters,
    RentalFlow,
    Reserve,
    reserve_outcome,
)
from flagman_load.run_state import STATE_ENV, ClientSession, RunState
from flagman_load.stream_clock import StreamClock
from flagman_load.thresholds import RunSummary
from flagman_load.zones import Zone

STATE = RunState.load(Path(os.environ[STATE_ENV]))
PROFILE = PROFILES[STATE.profile]
ZONES = [Zone(zone["kind"], zone["polygon"]["coordinates"]) for zone in STATE.zones]
PHOTO_BYTES = make_photo()

_MAP_SPREAD_DEG = 0.03
_NO_CLIENT_PAUSE_S = 2.0
# Several telemetry intervals: silence this long means the stream is stuck, not idle.
_STREAM_TIMEOUT_S = 30.0
# Locust's event hooks have no type hints.
_EVENTS: Any = events

_renters = deque(STATE.renters)
_map_clients = deque(STATE.map_clients)
_otp_phones = deque(enumerate(STATE.otp_phones))
_staff_tokens = deque(STATE.staff_tokens)

_rentals = RentalCounters()
_telemetry_messages = 0
_stopping = False


def _failure(name: str, reason: str) -> None:
    _EVENTS.request.fire(
        request_type="FLOW",
        name=name,
        response_time=0,
        response_length=0,
        exception=RuntimeError(reason),
        context={},
    )


def _measurement(name: str, value_ms: float) -> None:
    _EVENTS.request.fire(
        request_type="WS",
        name=name,
        response_time=value_ms,
        response_length=0,
        exception=None,
        context={},
    )


def _failure_reason(response: Any) -> str:
    # Status 0 means there was no answer at all, and only the exception says why.
    if response.status_code == 0:
        return f"no answer: {type(response.error).__name__}"
    return f"{response.status_code} {error_code(response)}"


def _write_summary(environment: Any, **_: Any) -> None:
    summary = RunSummary(_rentals.peak, _rentals.finished, _rentals.unpaid, _telemetry_messages)
    Path(STATE.summary_path).write_text(json.dumps(asdict(summary)), encoding="utf-8")


def _mark_stopping(environment: Any, **_: Any) -> None:
    global _stopping
    _stopping = True


_EVENTS.quitting.add_listener(_write_summary)
_EVENTS.test_stopping.add_listener(_mark_stopping)


class _ClientUser(HttpUser):
    abstract = True
    host = STATE.base_url

    def _headers(self, session: ClientSession) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {session.access_token}"}
        if STATE.send_client_ip:
            headers["X-Forwarded-For"] = session.ip
        return headers

    def _refresh_if_due(self, session: ClientSession) -> None:
        if not session.needs_refresh(time.time()):
            return
        response = self.call(
            "POST",
            "/auth/token/refresh",
            "POST /auth/token/refresh",
            session,
            json={"refresh_token": session.refresh_token},
        )
        if response is not None:
            tokens = response.json()
            session.access_token = tokens["access_token"]
            session.refresh_token = tokens["refresh_token"]
            session.issued_at = time.time()

    def call(
        self,
        method: str,
        path: str,
        name: str,
        session: ClientSession,
        **kwargs: Any,
    ) -> Response | None:
        headers = {**self._headers(session), **kwargs.pop("headers", {})}
        with self.client.request(
            method, path, name=name, headers=headers, catch_response=True, **kwargs
        ) as response:
            if response.status_code == 200:
                response.success()
                reply: Response = response
                return reply
            response.failure(_failure_reason(response))
            return None


class MapUser(_ClientUser):
    """A client looking at the map: the most frequent request of the pilot."""

    fixed_count = PROFILE.map_users
    wait_time = between(*PROFILE.map_wait_s)

    def on_start(self) -> None:
        if not _map_clients:
            raise StopUser
        self.session = _map_clients.popleft()

    @task(5)
    def map_cars(self) -> None:
        self._refresh_if_due(self.session)
        self.call(
            "GET",
            "/map/cars",
            MAP_CARS,
            self.session,
            params={
                "lat": MAP_CENTER[0] + random.uniform(-_MAP_SPREAD_DEG, _MAP_SPREAD_DEG),
                "lon": MAP_CENTER[1] + random.uniform(-_MAP_SPREAD_DEG, _MAP_SPREAD_DEG),
                "limit": MAP_LIMIT,
            },
        )

    @task(1)
    def map_zones(self) -> None:
        self._refresh_if_due(self.session)
        self.call("GET", "/map/zones", MAP_ZONES, self.session)


class OtpUser(HttpUser):
    """Sign-ins. Each phone is one client IP too, so the limits per phone hold the rate."""

    fixed_count = PROFILE.otp_users
    host = STATE.base_url
    wait_time = between(*PROFILE.otp_wait_s)

    @task
    def sign_in(self) -> None:
        index, phone = _otp_phones.popleft()
        _otp_phones.append((index, phone))
        headers = {"X-Forwarded-For": client_ip(100_000 + index)} if STATE.send_client_ip else {}
        requested = self.client.post(
            "/auth/otp/request", json={"phone": phone}, headers=headers, name=OTP_REQUEST
        )
        # Without a code the check can only fail, and one lost request would count as two.
        if not requested.ok:
            return
        self.client.post(
            "/auth/otp/verify",
            json={"phone": phone, "code": STATE.otp_code, "device_id": str(uuid.uuid4())},
            headers=headers,
            name=OTP_VERIFY,
        )


class RenterUser(_ClientUser):
    """One whole rental after another, each with the next client of the pool."""

    fixed_count = PROFILE.renters
    wait_time = constant(1)

    def on_start(self) -> None:
        self.flow = RentalFlow(self, ZONES, PROFILE.trip_s, PHOTO_BYTES, _rentals, gevent.sleep)

    @task
    def rental(self) -> None:
        if not _renters:
            gevent.sleep(_NO_CLIENT_PAUSE_S)
            return
        session = _renters.popleft()
        self._refresh_if_due(session)
        if self.flow.run(session):
            _renters.append(session)

    def reserve(self, session: ClientSession, car_id: str) -> tuple[Reserve, dict[str, Any] | None]:
        with self.client.post(
            "/rentals",
            name=RESERVE,
            headers=self._headers(session),
            json={"car_id": car_id},
            catch_response=True,
        ) as response:
            outcome = reserve_outcome(response.status_code, error_code(response))
            if outcome is Reserve.FAILED:
                response.failure(_failure_reason(response))
                return outcome, None
            response.success()
            return outcome, response.json() if outcome is Reserve.RESERVED else None


class StaffStreamUser(User):
    """A staff member with the live fleet open; also how telemetry delay is observed."""

    fixed_count = PROFILE.staff_streams
    wait_time = constant(1)

    def on_start(self) -> None:
        # The stand has one account per role, so its sockets share a token.
        self.token = _staff_tokens[0]
        _staff_tokens.rotate(-1)

    @task
    def stream(self) -> None:
        started = time.perf_counter()
        socket = None
        try:
            socket = websocket.create_connection(
                STATE.ws_url, origin=STATE.origin, timeout=_STREAM_TIMEOUT_S
            )
            socket.send(json.dumps({"type": "auth", "token": self.token}))
            self._read_stream(socket, started)
        except (websocket.WebSocketException, OSError, ValueError) as error:
            _failure("WS /admin/fleet/stream", f"{type(error).__name__}: {error}")
        finally:
            if socket is not None:
                socket.close()

    def _read_stream(self, socket: websocket.WebSocket, started: float) -> None:
        global _telemetry_messages
        clock = StreamClock()
        # Without this the socket never ends its task, and every run waits out the stop timeout.
        while not _stopping:
            message = json.loads(socket.recv())
            received_at = datetime.now(UTC)
            if message["type"] == "snapshot":
                _measurement(STREAM_SNAPSHOT, (time.perf_counter() - started) * 1000)
                clock.observe(received_at, datetime.fromisoformat(message["as_of"]))
            elif message["type"] == "cars":
                clock.observe(received_at, datetime.fromisoformat(message["as_of"]))
                # One car is the answer to a command. Telemetry is the whole fleet at once, the
                # heavy path, and those few messages must not be outvoted in its percentile.
                if len(message["items"]) == 1:
                    continue
                lag = clock.lag_ms(received_at, _recorded(message["items"]))
                if lag is not None:
                    _telemetry_messages += 1
                    _measurement(TELEMETRY_LAG, lag)


def _recorded(items: list[dict[str, Any]]) -> list[datetime]:
    return [
        datetime.fromisoformat(item["state"]["recorded_at"])
        for item in items
        if item["state"] is not None
    ]
