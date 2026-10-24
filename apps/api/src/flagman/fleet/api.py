from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.resources import Resources, get_resources
from flagman.fleet import service
from flagman.fleet.domain import TelemetryTooLargeError, TelemetryUnavailableError
from flagman.fleet.emulator import EmulatorTelemetryReceiver
from flagman.fleet.ports import TelemetryReceiver, TelemetryWebhook
from flagman.fleet.schemas import MAX_TELEMETRY_BODY_BYTES, TelemetryReceived

router = APIRouter(prefix="/fleet", tags=["fleet"])


def get_telemetry_receiver(
    resources: Annotated[Resources, Depends(get_resources)],
) -> TelemetryReceiver:
    secret = resources.settings.emulator_telemetry_secret
    if secret is None:
        raise TelemetryUnavailableError("No telemetry secret is configured.")
    return EmulatorTelemetryReceiver(
        secret=secret.get_secret_value().encode(),
        tolerance_s=resources.settings.telematics_signature_tolerance_seconds,
    )


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_TELEMETRY_BODY_BYTES:
        raise TelemetryTooLargeError
    body = bytearray()
    # Chunked requests have no length up front, so the limit is also enforced while reading.
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_TELEMETRY_BODY_BYTES:
            raise TelemetryTooLargeError
    return bytes(body)


def _target(request: Request) -> bytes:
    # The bytes the client sent: a decoded and re-encoded path could differ from what was signed.
    path: bytes = request.scope.get("raw_path") or request.url.path.encode()
    query: bytes = request.scope.get("query_string", b"")
    return path + b"?" + query if query else path


# Called by telematics, not by our clients: kept out of the OpenAPI schema and the generated client.
@router.post("/telemetry", include_in_schema=False)
async def receive_telemetry(
    request: Request,
    receiver: Annotated[TelemetryReceiver, Depends(get_telemetry_receiver)],
    resources: Annotated[Resources, Depends(get_resources)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TelemetryReceived:
    webhook = TelemetryWebhook(
        method=request.method,
        target=_target(request),
        headers=request.headers,
        body=await _read_body(request),
    )
    now = datetime.now(UTC)
    try:
        snapshots = receiver.parse(webhook, now)
    except ValidationError as error:
        raise RequestValidationError(
            [{**item, "loc": ("body", *item["loc"])} for item in error.errors(include_input=False)]
        ) from error
    report = await service.receive_telemetry(
        db, snapshots, now, service.telemetry_clock_skew(resources.settings)
    )
    return TelemetryReceived(
        applied=report.applied,
        stale=report.stale,
        unknown_car=report.unknown_car,
        from_future=report.from_future,
    )
