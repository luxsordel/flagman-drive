"""Demo fleet for the pilot: Chinese EVs parked in Minsk, matched to the emulator's cars.

Specs are rounded from the makers' sheets; plates and VINs are made up and follow the formats only.
"""

from dataclasses import dataclass

from flagman.fleet.domain import CarModelSpec


@dataclass(frozen=True)
class DemoModel:
    spec: CarModelSpec
    # World manufacturer identifier and the descriptor part of the VIN; the serial is appended.
    vin_prefix: str


DEMO_MODELS = (
    DemoModel(CarModelSpec("BYD", "Dolphin", 5, 44_900, 340, None), "LGXCE4CB0R"),
    DemoModel(CarModelSpec("BYD", "Atto 3", 5, 60_500, 420, None), "LGXCE6CD1R"),
    DemoModel(CarModelSpec("Geely", "Geometry C", 5, 70_000, 550, None), "L6T7944S2R"),
    DemoModel(CarModelSpec("Zeekr", "X", 5, 66_000, 440, None), "L6TCX2E73R"),
)

# Minsk city is region 7 on Belarusian plates; the numbers leave room for the emulator's limit
# of 1000 cars.
_PLATE_SERIES = "EX-7"
MAX_DEMO_CARS = 9999


def demo_plate(index: int) -> str:
    return f"{index + 1:04d} {_PLATE_SERIES}"


def demo_vin(index: int) -> str:
    return f"{DEMO_MODELS[index % len(DEMO_MODELS)].vin_prefix}{index + 1:07d}"
