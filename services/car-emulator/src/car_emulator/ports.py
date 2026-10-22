from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from car_emulator.schemas import TelemetryBatch

Clock = Callable[[], datetime]


class TelemetrySink(Protocol):
    async def send(self, batch: TelemetryBatch) -> None: ...
