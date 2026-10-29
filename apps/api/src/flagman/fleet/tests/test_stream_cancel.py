import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocket

from flagman.core.config import Settings
from flagman.fleet.domain import AvailabilityRules
from flagman.fleet.stream import FleetHub, StaffStream, Subscription, _Signed
from flagman.identity.domain import StaffAccess, StaffAccessClaims, StaffRole


class _ClosingSocket:
    """The client has closed already; a send in flight still takes a moment to wind down."""

    def __init__(self) -> None:
        self.scope: anyio.CancelScope | None = None

    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, data: str) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # The server is cancelled from outside right while it waits for its own tasks to end:
            # the test client does this when it leaves the socket, uvicorn on shutdown.
            # Raise at once: an await here would let anyio cancel this task too, and its own
            # cancel message would make even gather hand the caller a cancel the scope recognises.
            assert self.scope is not None
            self.scope.cancel()
            raise


def _stream(socket: _ClosingSocket) -> StaffStream:
    return StaffStream(
        websocket=cast(WebSocket, socket),
        hub=cast(FleetHub, None),
        settings=cast(Settings, None),
        session_factory=cast(async_sessionmaker[AsyncSession], None),
        rules=cast(AvailabilityRules, None),
        roles=frozenset(StaffRole),
    )


def _signed() -> _Signed:
    claims = StaffAccessClaims(staff_id=uuid.uuid4(), session_id=uuid.uuid4(), role=StaffRole.ADMIN)
    return _Signed(StaffAccess(claims=claims, expires_at=datetime.now(UTC) + timedelta(hours=1)))


async def test_an_outside_cancel_while_tasks_wind_down_stays_a_cancel_of_the_caller() -> None:
    socket = _ClosingSocket()
    subscription = Subscription()
    subscription.offer("{}")

    with anyio.CancelScope() as scope:
        socket.scope = scope
        await _stream(socket)._run(_signed(), subscription)

    # Without this the cancel scope cannot tell the cancel is its own, and it escapes the caller.
    assert scope.cancelled_caught
