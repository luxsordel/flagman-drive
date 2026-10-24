from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from flagman.fleet.domain import TelemetrySnapshot


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
