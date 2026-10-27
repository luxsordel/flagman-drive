"""Demo tariff for the pilot: placeholders until the business sets the prices (docs/01)."""

from flagman.pricing.domain import DEFAULT_FREE_WAITING_MINUTES, TariffPrices

# Kopecks of BYN: 0.39 a minute driving, 0.12 parked or waiting, a 30 BYN hold.
DEMO_PRICES = TariffPrices(
    driving_per_minute=39,
    parked_per_minute=12,
    waiting_per_minute=12,
    free_waiting_minutes=DEFAULT_FREE_WAITING_MINUTES,
    hold_amount=3000,
)
