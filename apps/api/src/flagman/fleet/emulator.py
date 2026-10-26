"""Adapters for services/car-emulator: telemetry in, commands out, HMAC-signed (docs/adr/0007)."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from uuid import UUID

import httpx2
import structlog
from pydantic import ValidationError

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import (
    CarCommand,
    CarCommandDuplicateError,
    CarCommandRejectedError,
    CarState,
    TelematicsTimeoutError,
    TelematicsUnavailableError,
    TelemetrySignatureInvalidError,
    TelemetrySnapshot,
)
from flagman.fleet.ports import TelemetryWebhook
from flagman.fleet.schemas import EmulatorCarState, EmulatorCommandResult, EmulatorTelemetryBatch
from flagman.fleet.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SignatureError,
    SignedRequest,
    sign,
    verify,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class EmulatorTelemetryReceiver:
    secret: bytes
    tolerance_s: int

    def parse(self, webhook: TelemetryWebhook, now: datetime) -> list[TelemetrySnapshot]:
        try:
            request = SignedRequest(
                method=webhook.method,
                target=webhook.target,
                body=webhook.body,
                timestamp_header=webhook.headers.get(TIMESTAMP_HEADER),
                signature_header=webhook.headers.get(SIGNATURE_HEADER),
            )
            verify(self.secret, request, now=now, tolerance_s=self.tolerance_s)
        except SignatureError as error:
            # A skewed clock and a wrong secret look alike to the caller; the log tells them apart.
            logger.warning("telemetry_signature_rejected", reason=str(error))
            raise TelemetrySignatureInvalidError from error
        batch = EmulatorTelemetryBatch.model_validate_json(webhook.body)
        return [TelemetrySnapshot(telematics_id=car.id, state=_state(car)) for car in batch.cars]


def _state(car: EmulatorCarState) -> CarState:
    return CarState(
        location=GeoPoint(lat=car.lat, lon=car.lon),
        battery_pct=car.battery_pct,
        doors_locked=car.doors_locked,
        ignition_on=car.ignition_on,
        engine_locked=car.engine_locked,
        recorded_at=car.updated_at,
    )


_DUPLICATE = "duplicate_command"
_REJECTED_PREFIX = "command_rejected:"
# Our own detail codes are short; anything longer is not worth a log line.
_LOGGED_DETAIL_LENGTH = 64

# The request never left: the car surely did nothing.
_NOT_SENT = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class EmulatorTelematicsGateway:
    """`POST /cars/{id}/commands` of the emulator."""

    client: httpx2.AsyncClient
    base_url: str
    secret: bytes
    # For the whole command: the client's own timeouts count each phase and each read apart.
    timeout_s: float
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def send(
        self, telematics_id: UUID, command_id: UUID, command: CarCommand
    ) -> CarState | None:
        url = httpx2.URL(f"{self.base_url}/cars/{telematics_id}/commands")
        body = json.dumps({"command_id": str(command_id), "type": command.value}).encode()
        timestamp = int(self.clock().timestamp())
        headers = {
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign(self.secret, timestamp, "POST", url.raw_path, body),
        }
        try:
            async with asyncio.timeout(self.timeout_s):
                response = await self.client.post(url, content=body, headers=headers)
        except _NOT_SENT as error:
            logger.warning("telematics_unreachable", error=type(error).__name__)
            raise TelematicsUnavailableError from error
        except (TimeoutError, httpx2.TransportError) as error:
            # Maybe sent, and the answer is lost: the car may have done it.
            logger.warning("telematics_no_answer", error=type(error).__name__)
            raise TelematicsTimeoutError from error
        if response.status_code == HTTPStatus.OK:
            try:
                return _state(EmulatorCommandResult.model_validate_json(response.content).car)
            except ValidationError as error:
                # The car did it, only its state is unreadable: the outcome is not "nothing".
                logger.error("telematics_answer_invalid")
                raise TelematicsTimeoutError from error
        detail = _detail(response)
        if response.status_code == HTTPStatus.CONFLICT and detail == _DUPLICATE:
            raise CarCommandDuplicateError
        if response.status_code == HTTPStatus.CONFLICT and detail.startswith(_REJECTED_PREFIX):
            raise CarCommandRejectedError(detail.removeprefix(_REJECTED_PREFIX))
        logger.error(
            "telematics_command_failed",
            status=response.status_code,
            detail=detail[:_LOGGED_DETAIL_LENGTH],
        )
        # A 4xx comes before the command runs: a wrong secret (401), a car the emulator does not
        # run (404). A 5xx may come after it did.
        if response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
            raise TelematicsUnavailableError
        raise TelematicsTimeoutError


def _detail(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) else ""
