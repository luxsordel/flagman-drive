"""One whole rental, apart from Locust: importing Locust patches the network modules for good."""

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from flagman_load.profiles import FINISH, INSPECTION_PHOTOS, MAP_CARS, OPEN, PHOTO
from flagman_load.run_state import ClientSession
from flagman_load.zones import Zone, can_finish_at

MAP_CENTER = (53.9023, 27.5615)
MAP_LIMIT = 100
_CARS_TO_TRY = 5
_NO_CAR_PAUSE_S = 2.0
_TRIP_POLL_S = 10.0
_FINISH_ATTEMPTS = 3
_FINISH_RETRY_S = 20.0
_CURRENT = "GET /rentals/{id}"
_CANCEL = "POST /rentals/{id}/cancel"
_SUBMIT = "POST /rentals/{id}/inspections/{stage}/submit"
# The API's answer to a car someone took a moment ago: a car not rentable looks like a missing one.
_LOST_RACE_CODE = "car-not-found"


class Reserve(Enum):
    RESERVED = "reserved"
    LOST_RACE = "lost race"
    FAILED = "failed"


def reserve_outcome(status_code: int, code: str) -> Reserve:
    if status_code == 201:
        return Reserve.RESERVED
    return Reserve.LOST_RACE if code == _LOST_RACE_CODE else Reserve.FAILED


class Reply(Protocol):
    def json(self) -> Any: ...


class RentalApi(Protocol):
    def call(
        self, method: str, path: str, name: str, session: ClientSession, **kwargs: Any
    ) -> Reply | None:
        """The answer if it is a 200; any other is recorded as a failure."""
        ...

    def reserve(self, session: ClientSession, car_id: str) -> tuple[Reserve, dict[str, Any] | None]:
        """The rental, when the outcome is `RESERVED`."""
        ...


@dataclass
class RentalCounters:
    active: int = 0
    peak: int = 0
    finished: int = 0
    unpaid: int = 0


class RentalFlow:
    def __init__(
        self,
        api: RentalApi,
        zones: Sequence[Zone],
        trip_s: tuple[float, float],
        photo: bytes,
        counters: RentalCounters,
        sleep: Callable[[float], object],
    ) -> None:
        self._api = api
        self._zones = zones
        self._trip_s = trip_s
        self._photo = photo
        self._counters = counters
        self._sleep = sleep

    def run(self, session: ClientSession) -> bool:
        """True if the client can rent again; one left with a rental or a debt cannot reserve."""
        rental = self._reserve(session)
        if rental is None:
            return True
        rental_id = rental["id"]
        if not self._inspect(session, rental_id, "before"):
            return self._cancel(session, rental_id)
        car = self._car_to_open(session, rental_id)
        if car is None:
            return self._cancel(session, rental_id)
        opened = self._api.call(
            "POST",
            f"/rentals/{rental_id}/commands",
            OPEN,
            session,
            json={"command": "open", "lat": car["lat"], "lon": car["lon"]},
        )
        if opened is None:
            return self._cancel(session, rental_id)
        return self._ride_and_finish(session, rental_id)

    def _reserve(self, session: ClientSession) -> dict[str, Any] | None:
        cars = self._api.call(
            "GET",
            "/map/cars",
            MAP_CARS,
            session,
            params={"lat": MAP_CENTER[0], "lon": MAP_CENTER[1], "limit": MAP_LIMIT},
        )
        if cars is None:
            return None
        usable = [
            car
            for car in cars.json()["items"]
            if can_finish_at(self._zones, car["lat"], car["lon"])
        ]
        random.shuffle(usable)
        for car in usable[:_CARS_TO_TRY]:
            outcome, rental = self._api.reserve(session, car["id"])
            if outcome is Reserve.RESERVED:
                return rental
            if outcome is Reserve.FAILED:
                return None
        self._sleep(_NO_CAR_PAUSE_S)
        return None

    def _inspect(self, session: ClientSession, rental_id: str, stage: str) -> bool:
        for _ in range(INSPECTION_PHOTOS):
            uploaded = self._api.call(
                "POST",
                f"/rentals/{rental_id}/inspections/{stage}/photos",
                PHOTO,
                session,
                data=self._photo,
                headers={"Content-Type": "image/jpeg"},
            )
            if uploaded is None:
                return False
        path = f"/rentals/{rental_id}/inspections/{stage}/submit"
        return self._api.call("POST", path, _SUBMIT, session) is not None

    def _car_to_open(self, session: ClientSession, rental_id: str) -> dict[str, Any] | None:
        """Where the car is now, if the rental can still end there.

        The emulator does not know about reservations, so on the stand a reserved car may have
        driven off during the photos. Far from it the command is refused, and outside the zone the
        rental could never end.
        """
        current = self._api.call("GET", f"/rentals/{rental_id}", _CURRENT, session)
        if current is None:
            return None
        car: dict[str, Any] = current.json()["car"]
        if car["lat"] is None or not can_finish_at(self._zones, car["lat"], car["lon"]):
            return None
        return car

    def _ride_and_finish(self, session: ClientSession, rental_id: str) -> bool:
        counters = self._counters
        counters.active += 1
        counters.peak = max(counters.peak, counters.active)
        try:
            self._ride(session, rental_id)
            if not self._inspect(session, rental_id, "after"):
                return False
            status = self._finish(session, rental_id)
        finally:
            counters.active -= 1
        if status == "paid":
            counters.finished += 1
            return True
        if status is not None:
            counters.unpaid += 1
        return False

    def _ride(self, session: ClientSession, rental_id: str) -> None:
        trip_ends = time.monotonic() + random.uniform(*self._trip_s)
        while (left := trip_ends - time.monotonic()) > 0:
            self._sleep(min(left, _TRIP_POLL_S))
            self._api.call("GET", f"/rentals/{rental_id}", _CURRENT, session)

    def _finish(self, session: ClientSession, rental_id: str) -> str | None:
        for attempt in range(_FINISH_ATTEMPTS):
            if attempt:
                self._sleep(_FINISH_RETRY_S)
            finished = self._api.call("POST", f"/rentals/{rental_id}/finish", FINISH, session)
            if finished is not None:
                return str(finished.json()["status"])
        return None

    def _cancel(self, session: ClientSession, rental_id: str) -> bool:
        path = f"/rentals/{rental_id}/cancel"
        return self._api.call("POST", path, _CANCEL, session) is not None
