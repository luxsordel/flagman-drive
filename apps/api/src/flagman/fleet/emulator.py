"""Telemetry adapter for services/car-emulator: HMAC signature (docs/adr/0007) and its batch."""

from dataclasses import dataclass
from datetime import datetime

import structlog

from flagman.fleet.domain import (
    CarState,
    GeoPoint,
    TelemetrySignatureInvalidError,
    TelemetrySnapshot,
)
from flagman.fleet.ports import TelemetryWebhook
from flagman.fleet.schemas import EmulatorTelemetryBatch
from flagman.fleet.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SignatureError,
    SignedRequest,
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
        return [
            TelemetrySnapshot(
                telematics_id=car.id,
                state=CarState(
                    location=GeoPoint(lat=car.lat, lon=car.lon),
                    battery_pct=car.battery_pct,
                    doors_locked=car.doors_locked,
                    ignition_on=car.ignition_on,
                    recorded_at=car.updated_at,
                ),
            )
            for car in batch.cars
        ]
