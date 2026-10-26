from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from flagman.fleet.domain import CarCommand, CarState, TelemetrySnapshot


@dataclass(frozen=True)
class TelemetryWebhook:
    """A telemetry request as it came, before anything in it is trusted."""

    method: str
    # Path and query string, bytes as sent: providers sign what they sent, not a normalized URL.
    target: bytes
    # Looked up case-insensitively, as Starlette's request headers are.
    headers: Mapping[str, str]
    body: bytes


class TelemetryReceiver(Protocol):
    """Telemetry webhooks of one telematics provider (docs/02-architecture.md).

    The emulator's adapter is flagman.fleet.emulator; a real provider gets an adapter of its own
    with its own signature and body format, and the rest of the fleet stays as it is.
    """

    def parse(self, webhook: TelemetryWebhook, now: datetime) -> list[TelemetrySnapshot]:
        """Checks that the provider sent the request, then reads the snapshots from the body.

        Raises TelemetrySignatureInvalidError before the body is parsed, and
        pydantic.ValidationError for a body that is not a valid batch.
        """
        ...


class TelematicsGateway(Protocol):
    """Commands to the cars of one telematics provider (docs/02-architecture.md).

    The emulator's adapter is flagman.fleet.emulator, like the telemetry receiver.
    """

    async def send(
        self, telematics_id: UUID, command_id: UUID, command: CarCommand
    ) -> CarState | None:
        """Sends one command and waits for the car; returns its state after, if the provider tells.

        `command_id` is fresh for every call: the provider refuses an id it has seen (ADR-0007).
        Raises CarCommandRejectedError, CarCommandDuplicateError, TelematicsUnavailableError and
        TelematicsTimeoutError; never hangs longer than the adapter's timeout.
        """
        ...
