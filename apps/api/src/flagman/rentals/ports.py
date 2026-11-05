"""What rentals need from the fines, which live in `support` (docs/adr/0028).

`support` reads rentals through `rentals.service`, so rentals cannot import it back; the API wires
the implementation in instead.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flagman.rentals.domain import Rental


class Fines(Protocol):
    async def has_unpaid(self, db: AsyncSession, user_id: UUID) -> bool:
        """An unpaid fine is a debt: a new reservation waits until it is paid."""
        ...

    async def trip_finished(self, db: AsyncSession, rental: Rental, now: datetime) -> None:
        """Called in the transaction that ends a trip the client finished, after the car state
        at the end is saved; charges the automatic fines the trip earned."""
        ...
