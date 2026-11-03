from dataclasses import dataclass, field
from typing import Any

import pytest

from flagman_load.profiles import FINISH, INSPECTION_PHOTOS, OPEN, PHOTO
from flagman_load.rental_flow import (
    RentalCounters,
    RentalFlow,
    Reply,
    Reserve,
    reserve_outcome,
)
from flagman_load.run_state import ClientSession
from flagman_load.zones import Zone

SESSION = ClientSession("10.0.0.1", "access", "refresh")
SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
ZONES = [Zone("allowed", [SQUARE])]
IN_ZONE = {"id": "in", "lat": 1.0, "lon": 1.0}
OUTSIDE = {"id": "out", "lat": 50.0, "lon": 50.0}
CANCEL = "POST /rentals/{id}/cancel"
RENTAL = {"id": "r1"}


@dataclass
class FakeReply:
    body: Any

    def json(self) -> Any:
        return self.body


@dataclass
class FakeApi:
    """Answers each request name with the next scripted body; None is a recorded failure."""

    cars: list[dict[str, Any]] = field(default_factory=lambda: [IN_ZONE])
    car_now: dict[str, Any] = field(default_factory=lambda: IN_ZONE)
    reserves: list[Reserve] = field(default_factory=lambda: [Reserve.RESERVED])
    finishes: list[str | None] = field(default_factory=lambda: ["paid"])
    failing: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)

    def call(
        self, method: str, path: str, name: str, session: ClientSession, **kwargs: Any
    ) -> Reply | None:
        self.calls.append(name)
        if name in self.failing:
            return None
        if name == FINISH:
            status = self.finishes.pop(0)
            return None if status is None else FakeReply({"status": status})
        if name == "GET /map/cars":
            return FakeReply({"items": self.cars})
        if name == "GET /rentals/{id}":
            return FakeReply({"car": self.car_now})
        return FakeReply({})

    def reserve(self, session: ClientSession, car_id: str) -> tuple[Reserve, dict[str, Any] | None]:
        self.tried.append(car_id)
        outcome = self.reserves.pop(0)
        return outcome, RENTAL if outcome is Reserve.RESERVED else None


def _run(api: FakeApi) -> tuple[bool, RentalCounters, list[float]]:
    counters = RentalCounters()
    sleeps: list[float] = []
    flow = RentalFlow(api, ZONES, (0.0, 0.0), b"photo", counters, sleeps.append)
    return flow.run(SESSION), counters, sleeps


def test_a_paid_rental_counts_and_the_client_stays_in_the_pool() -> None:
    api = FakeApi()
    stays, counters, _ = _run(api)
    assert stays is True
    assert (counters.finished, counters.unpaid, counters.peak, counters.active) == (1, 0, 1, 0)
    assert api.calls.count(PHOTO) == 2 * INSPECTION_PHOTOS


def test_an_unpaid_rental_counts_apart_and_the_client_leaves_the_pool() -> None:
    stays, counters, _ = _run(FakeApi(finishes=["payment_failed"]))
    assert stays is False
    assert (counters.finished, counters.unpaid) == (0, 1)


def test_a_finish_is_retried_after_a_pause_and_can_still_succeed() -> None:
    api = FakeApi(finishes=[None, "paid"])
    stays, counters, sleeps = _run(api)
    assert stays is True
    assert counters.finished == 1
    assert api.calls.count(FINISH) == 2
    assert sleeps == [20.0]


def test_a_finish_refused_every_time_leaves_the_client_out_of_the_pool() -> None:
    api = FakeApi(finishes=[None, None, None])
    stays, counters, _ = _run(api)
    assert stays is False
    assert (counters.finished, counters.unpaid, counters.active) == (0, 0, 0)
    assert api.calls.count(FINISH) == 3


def test_a_lost_race_tries_the_next_car() -> None:
    second = {"id": "second", "lat": 2.0, "lon": 2.0}
    api = FakeApi(cars=[IN_ZONE, second], reserves=[Reserve.LOST_RACE, Reserve.RESERVED])
    _, counters, _ = _run(api)
    assert set(api.tried) == {"in", "second"}
    assert counters.finished == 1


def test_a_refused_reservation_stops_at_once_and_keeps_the_client() -> None:
    api = FakeApi(cars=[IN_ZONE, IN_ZONE], reserves=[Reserve.FAILED])
    stays, counters, _ = _run(api)
    assert stays is True
    assert len(api.tried) == 1
    assert counters.finished == 0


def test_only_cars_in_the_zone_are_reserved() -> None:
    api = FakeApi(cars=[OUTSIDE])
    stays, _, sleeps = _run(api)
    assert stays is True
    assert api.tried == []
    assert sleeps == [2.0]


def test_a_map_failure_reserves_nothing() -> None:
    api = FakeApi(failing={"GET /map/cars"})
    assert _run(api)[0] is True
    assert api.tried == []


def test_a_car_that_drove_out_of_the_zone_is_cancelled_before_opening() -> None:
    api = FakeApi(car_now=OUTSIDE)
    stays, counters, _ = _run(api)
    assert stays is True
    assert OPEN not in api.calls
    assert CANCEL in api.calls
    assert counters.peak == 0


def test_a_car_with_no_position_is_cancelled_before_opening() -> None:
    api = FakeApi(car_now={"id": "in", "lat": None, "lon": None})
    assert _run(api)[0] is True
    assert OPEN not in api.calls
    assert CANCEL in api.calls


@pytest.mark.parametrize("failing", [PHOTO, OPEN, "GET /rentals/{id}"])
def test_a_failure_before_the_trip_cancels_the_reservation(failing: str) -> None:
    api = FakeApi(failing={failing})
    stays, counters, _ = _run(api)
    assert stays is True
    assert CANCEL in api.calls
    assert counters.peak == 0


def test_a_cancel_that_fails_leaves_the_client_out_of_the_pool() -> None:
    assert _run(FakeApi(failing={OPEN, CANCEL}))[0] is False


def test_a_failed_photo_after_the_trip_leaves_the_rental_open() -> None:
    counters = RentalCounters()

    class FailsAfterTrip(FakeApi):
        def call(
            self, method: str, path: str, name: str, session: ClientSession, **kwargs: Any
        ) -> Reply | None:
            if "/inspections/after/" in path:
                self.calls.append(name)
                return None
            return super().call(method, path, name, session, **kwargs)

    api = FailsAfterTrip()
    flow = RentalFlow(api, ZONES, (0.0, 0.0), b"photo", counters, lambda _: None)
    assert flow.run(SESSION) is False
    assert FINISH not in api.calls
    assert (counters.finished, counters.active, counters.peak) == (0, 0, 1)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (201, "", Reserve.RESERVED),
        (404, "car-not-found", Reserve.LOST_RACE),
        (409, "active-rental-exists", Reserve.FAILED),
        (409, "outstanding-debt", Reserve.FAILED),
        (429, "rate-limited", Reserve.FAILED),
        (0, "", Reserve.FAILED),
    ],
)
def test_reserve_outcome(status: int, code: str, expected: Reserve) -> None:
    assert reserve_outcome(status, code) is expected
