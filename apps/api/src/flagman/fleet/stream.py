"""Live fleet changes for staff over WebSocket (docs/adr/0017).

Every API process listens to fleet.changes.CHANNEL in Postgres and passes what changed to the
sockets it holds. Notifications are sent on commit, so a rolled back change never reaches staff,
and a process that missed some (its listening connection dropped) closes its sockets: each one
reconnects and starts again from a full snapshot.
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg
import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from flagman.core.config import Settings
from flagman.fleet import service
from flagman.fleet.changes import CHANNEL, decode_notices
from flagman.fleet.domain import AvailabilityRules, StaffCarFilter
from flagman.fleet.schemas import (
    STREAM_MESSAGE_MAX_CHARS,
    StaffCarOut,
    StreamAuth,
    StreamCars,
    StreamCommand,
    StreamCommandOut,
    StreamSnapshot,
)
from flagman.identity import service as identity
from flagman.identity.domain import (
    AccessTokenInvalidError,
    StaffAccess,
    StaffRole,
    StaffRoleForbiddenError,
)

logger = structlog.get_logger(__name__)

# Close codes: 4401 and 4403 mirror HTTP, the client signs in again or gives up; on 1013 it
# reconnects at once and gets a fresh snapshot.
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_TRY_AGAIN = 1013
CLOSE_POLICY = 1008
CLOSE_GOING_AWAY = 1001

AUTH_TIMEOUT_S = 10.0
# How soon a revoked session or a disabled account loses the stream; tokens alone allow 15 min.
SESSION_CHECK_INTERVAL_S = 30.0
# Telemetry comes every few seconds as one message: this is minutes of backlog.
SUBSCRIBER_QUEUE_SIZE = 64
_INBOX_SIZE = 1024
_CONNECT_TIMEOUT_S = 5.0
_HEARTBEAT_INTERVAL_S = 30.0
_RECONNECT_DELAY_S = 2.0
_CLOSE_TIMEOUT_S = 5.0


@dataclass(eq=False)
class Subscription:
    """One socket's queue of messages; a socket that falls behind is dropped, not waited for."""

    queue: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    )
    dropped: asyncio.Event = field(default_factory=asyncio.Event)
    close_code: int = CLOSE_TRY_AGAIN

    def offer(self, message: str) -> None:
        if self.dropped.is_set():
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.drop(CLOSE_TRY_AGAIN)

    def drop(self, close_code: int) -> None:
        if not self.dropped.is_set():
            self.close_code = close_code
            self.dropped.set()


class FleetHub:
    """The process's listener of fleet changes and the sockets it passes them to."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._subscriptions: set[Subscription] = set()
        self._listening = asyncio.Event()
        self._inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=_INBOX_SIZE)

    @property
    def listening(self) -> bool:
        return self._listening.is_set()

    def subscribe(self) -> Subscription | None:
        """None while the process does not listen: changes made meanwhile would be lost."""
        if not self._listening.is_set():
            return None
        subscription = Subscription()
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions.discard(subscription)

    def publish(self, message: str) -> None:
        for subscription in list(self._subscriptions):
            subscription.offer(message)
            if subscription.dropped.is_set():
                self._subscriptions.discard(subscription)

    def drop_all(self, close_code: int) -> None:
        for subscription in self._subscriptions:
            subscription.drop(close_code)
        self._subscriptions.clear()

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._listen_forever())
                tasks.create_task(self._deliver_forever())
        finally:
            self.drop_all(CLOSE_GOING_AWAY)

    async def _listen_forever(self) -> None:
        while True:
            try:
                await self._listen_once()
            except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as error:
                logger.warning("fleet_stream_listener_lost", error=type(error).__name__)
            except Exception:
                # A bug must not leave the process without its stream until the next restart.
                logger.exception("fleet_stream_listener_failed")
            # Whatever came while nobody listened is lost: every socket starts over.
            self.drop_all(CLOSE_TRY_AGAIN)
            await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _listen_once(self) -> None:
        settings = self._settings
        connection = await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            database=settings.postgres_db,
            timeout=_CONNECT_TIMEOUT_S,
        )
        try:
            await connection.add_listener(CHANNEL, self._on_notification)
            self._listening.set()
            logger.info("fleet_stream_listening")
            # A connection cut without a goodbye is found only by using it.
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                await connection.fetchval("SELECT 1", timeout=_CONNECT_TIMEOUT_S)
        finally:
            self._listening.clear()
            with contextlib.suppress(Exception):
                await connection.close(timeout=_CLOSE_TIMEOUT_S)

    def _on_notification(self, connection: object, pid: int, channel: str, payload: object) -> None:
        try:
            self._inbox.put_nowait(str(payload))
        except asyncio.QueueFull:
            # The database is too slow to read the changes back: better start everyone over.
            self.drop_all(CLOSE_TRY_AGAIN)

    async def _deliver_forever(self) -> None:
        while True:
            payloads = [await self._inbox.get()]
            while not self._inbox.empty():
                payloads.append(self._inbox.get_nowait())
            if not self._subscriptions:
                continue
            try:
                await self._deliver(payloads)
            except Exception:
                logger.exception("fleet_stream_delivery_failed")
                self.drop_all(CLOSE_TRY_AGAIN)

    async def _deliver(self, payloads: list[str]) -> None:
        notice = decode_notices(payloads)
        if notice.car_ids:
            # Read after the commit that sent the notice: never older than the database.
            async with self._session_factory() as db:
                cars = await service.get_cars(db, notice.car_ids)
            if cars:
                message = StreamCars(
                    items=[StaffCarOut.of(car) for car in cars], as_of=datetime.now(UTC)
                )
                self.publish(message.model_dump_json())
        # After the cars: the panel shows "done" next to the state the command left.
        for command in notice.commands:
            out = StreamCommandOut(
                command_id=command.command_id,
                car_id=command.car_id,
                command=command.command,
                outcome=command.outcome,
                reason=command.reason,
                staff_id=command.staff_id,
                at=command.at,
            )
            self.publish(StreamCommand(command=out).model_dump_json())


@dataclass
class _Signed:
    """Who holds the socket now; replaced when they send a renewed token."""

    access: StaffAccess
    renewed: asyncio.Event = field(default_factory=asyncio.Event)


class _RefusedError(Exception):
    def __init__(self, close_code: int) -> None:
        super().__init__(close_code)
        self.close_code = close_code


@dataclass(frozen=True)
class StaffStream:
    """One staff member's socket, from the Origin check to the close."""

    websocket: WebSocket
    hub: FleetHub
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    rules: AvailabilityRules
    roles: frozenset[StaffRole]

    async def serve(self) -> None:
        # Browsers send Origin with every WebSocket and CORS does not apply to sockets: without
        # this check any site the staff member opens could connect from their browser.
        foreign = self.websocket.headers.get("origin") not in self.settings.api_cors_origins
        # Refused after the handshake: a refused handshake reaches a browser as a bare 1006, the
        # same as a lost connection, and it would keep reconnecting. Nothing is sent before
        # the auth message, so the foreign page gets nothing but the close.
        await self.websocket.accept()
        if foreign:
            await self._close(CLOSE_POLICY)
            return
        try:
            signed = _Signed(await self._sign_in())
        except _RefusedError as refused:
            await self._close(refused.close_code)
            return
        subscription = self.hub.subscribe()
        if subscription is None:
            await self._close(CLOSE_TRY_AGAIN)
            return
        staff_id = str(signed.access.claims.staff_id)
        logger.info("fleet_stream_opened", staff_id=staff_id)
        close_code: int | None = CLOSE_TRY_AGAIN
        try:
            await self._send_snapshot()
            close_code = await self._run(signed, subscription)
        except WebSocketDisconnect:
            close_code = None
        finally:
            self.hub.unsubscribe(subscription)
        logger.info("fleet_stream_closed", staff_id=staff_id, close_code=close_code)
        if close_code is not None:
            await self._close(close_code)

    async def _sign_in(self) -> StaffAccess:
        try:
            token = await asyncio.wait_for(self._receive_auth(), AUTH_TIMEOUT_S)
        except TimeoutError:
            raise _RefusedError(CLOSE_UNAUTHORIZED) from None
        except WebSocketDisconnect:
            raise _RefusedError(CLOSE_GOING_AWAY) from None
        return await self._check(token, None)

    async def _receive_auth(self) -> str:
        message = await self.websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        text = message.get("text")
        if not isinstance(text, str):
            raise _RefusedError(CLOSE_POLICY)
        return self._parse_auth(text)

    def _parse_auth(self, text: str) -> str:
        # The text is never logged: it carries the token.
        if len(text) > STREAM_MESSAGE_MAX_CHARS:
            raise _RefusedError(CLOSE_POLICY)
        try:
            return StreamAuth.model_validate_json(text).token
        except ValidationError:
            raise _RefusedError(CLOSE_POLICY) from None

    async def _check(self, token: str, current: StaffAccess | None) -> StaffAccess:
        secret = self.settings.api_jwt_secret.get_secret_value()
        try:
            async with self.session_factory() as db:
                access = await identity.authenticate_staff(
                    db, token, secret, self.roles, datetime.now(UTC)
                )
        except AccessTokenInvalidError:
            raise _RefusedError(CLOSE_UNAUTHORIZED) from None
        except StaffRoleForbiddenError:
            raise _RefusedError(CLOSE_FORBIDDEN) from None
        if current is not None and access.claims.staff_id != current.claims.staff_id:
            raise _RefusedError(CLOSE_POLICY)
        return access

    async def _session_active(self, access: StaffAccess) -> bool:
        async with self.session_factory() as db:
            return await identity.is_staff_session_active(
                db, access.claims.staff_id, access.claims.session_id, datetime.now(UTC)
            )

    async def _send_snapshot(self) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as db:
            cars = await service.list_cars_for_staff(db, self.rules, StaffCarFilter())
        snapshot = StreamSnapshot.of(cars, self.rules, now)
        await self.websocket.send_text(snapshot.model_dump_json())

    async def _run(self, signed: _Signed, subscription: Subscription) -> int | None:
        """Returns the close code, or None when the client closed the socket itself."""
        tasks = {
            asyncio.create_task(self._send_changes(subscription)),
            asyncio.create_task(self._receive_renewals(signed)),
            asyncio.create_task(self._watch_session(signed)),
            asyncio.create_task(self._wait_dropped(subscription)),
        }
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finished = done.pop()
        error = finished.exception()
        if isinstance(error, _RefusedError):
            return error.close_code
        if isinstance(error, WebSocketDisconnect):
            return None
        if error is not None:
            raise error
        return finished.result()

    async def _send_changes(self, subscription: Subscription) -> int | None:
        while True:
            await self.websocket.send_text(await subscription.queue.get())

    async def _receive_renewals(self, signed: _Signed) -> int | None:
        while True:
            token = await self._receive_auth()
            signed.access = await self._check(token, signed.access)
            signed.renewed.set()

    async def _watch_session(self, signed: _Signed) -> int | None:
        next_check = datetime.now(UTC) + timedelta(seconds=SESSION_CHECK_INTERVAL_S)
        while True:
            now = datetime.now(UTC)
            if now >= signed.access.expires_at:
                return CLOSE_UNAUTHORIZED
            if now >= next_check:
                if not await self._session_active(signed.access):
                    return CLOSE_UNAUTHORIZED
                next_check = now + timedelta(seconds=SESSION_CHECK_INTERVAL_S)
            wake_at = min(signed.access.expires_at, next_check)
            signed.renewed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(signed.renewed.wait(), (wake_at - now).total_seconds())

    async def _wait_dropped(self, subscription: Subscription) -> int | None:
        await subscription.dropped.wait()
        return subscription.close_code

    async def _close(self, code: int) -> None:
        if self.websocket.client_state is WebSocketState.DISCONNECTED:
            return
        # A client that stopped reading would hold the close forever.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.websocket.close(code), _CLOSE_TIMEOUT_S)
