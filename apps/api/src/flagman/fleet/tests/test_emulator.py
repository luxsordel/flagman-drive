import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import (
    CarState,
    TelemetrySignatureInvalidError,
    TelemetrySnapshot,
)
from flagman.fleet.emulator import EmulatorTelemetryReceiver
from flagman.fleet.ports import TelemetryWebhook
from flagman.fleet.schemas import MAX_TELEMETRY_CARS
from flagman.fleet.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

SECRET = b"t" * 40
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
TARGET = b"/fleet/telemetry"
CAR_ID = uuid.UUID("3f1c6a52-6f0e-4b8e-9d55-2f0b7c9a1e10")
RECEIVER = EmulatorTelemetryReceiver(secret=SECRET, tolerance_s=300)


def car(**overrides: Any) -> dict[str, Any]:
    """One car as car_emulator.schemas.CarState serializes it."""
    return {
        "id": str(CAR_ID),
        "lat": 53.9006,
        "lon": 27.559,
        "battery_pct": 80,
        "doors_locked": True,
        "ignition_on": False,
        "engine_locked": False,
        "updated_at": (NOW - timedelta(seconds=2)).isoformat(),
    } | overrides


def batch(*cars: dict[str, Any]) -> bytes:
    return json.dumps({"sent_at": NOW.isoformat(), "cars": list(cars)}).encode()


def webhook(body: bytes, secret: bytes = SECRET) -> TelemetryWebhook:
    timestamp = int(NOW.timestamp())
    headers = {
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret, timestamp, "POST", TARGET, body),
    }
    return TelemetryWebhook(method="POST", target=TARGET, headers=headers, body=body)


def test_batch_becomes_snapshots() -> None:
    snapshots = RECEIVER.parse(webhook(batch(car())), NOW)

    assert snapshots == [
        TelemetrySnapshot(
            telematics_id=CAR_ID,
            state=CarState(
                location=GeoPoint(lat=53.9006, lon=27.559),
                battery_pct=80,
                doors_locked=True,
                ignition_on=False,
                engine_locked=False,
                recorded_at=NOW - timedelta(seconds=2),
            ),
        )
    ]


def test_signature_is_checked_before_the_body() -> None:
    # Not JSON at all: a parse error here would mean the body was read before the signature.
    with pytest.raises(TelemetrySignatureInvalidError):
        RECEIVER.parse(webhook(b"{not json", secret=b"o" * 40), NOW)


@pytest.mark.parametrize(
    "body",
    [
        b"{not json",
        batch(car(lat=91)),
        batch(car(lon=float("inf"))),
        batch(car(battery_pct=101)),
        batch(car(updated_at="2026-10-25T12:00:00")),
        batch(car(vin="WVWZZZ3CZWE123456")),
        batch(*[car()] * (MAX_TELEMETRY_CARS + 1)),
    ],
    ids=["not-json", "lat", "inf", "battery", "naive-time", "extra-field", "too-many-cars"],
)
def test_invalid_batch_is_refused(body: bytes) -> None:
    with pytest.raises(ValidationError):
        RECEIVER.parse(webhook(body), NOW)
