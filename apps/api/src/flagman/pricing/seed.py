"""Demo tariff and fines for the pilot: placeholders until the business sets them (docs/01)."""

from flagman.pricing.domain import (
    DEFAULT_FREE_WAITING_MINUTES,
    LOW_BATTERY_FINE_CODE,
    FineTerms,
    TariffPrices,
)

# Kopecks of BYN: 0.39 a minute driving, 0.12 parked or waiting, a 30 BYN hold.
DEMO_PRICES = TariffPrices(
    driving_per_minute=39,
    parked_per_minute=12,
    waiting_per_minute=12,
    free_waiting_minutes=DEFAULT_FREE_WAITING_MINUTES,
    hold_amount=3000,
)

# The fines docs/01 names, by code.
DEMO_FINES = {
    "smoking": FineTerms("Курение в салоне", "Smoking in the car", 10000),
    "dirt": FineTerms("Грязный салон", "Dirty interior", 5000),
    "parking_outside_zone": FineTerms("Парковка вне зоны", "Parking outside the zone", 5000),
    LOW_BATTERY_FINE_CODE: FineTerms(
        "Низкий заряд при завершении", "Low battery at the end of the trip", 2000
    ),
}
