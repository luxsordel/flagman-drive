import asyncio
import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import SettingsFactory
from flagman.fleet import stream
from flagman.fleet.changes import CARS_PER_NOTICE, decode_notices, encode_car_changes
from flagman.fleet.stream import FleetHub, Subscription


def test_car_changes_are_split_into_notices_postgres_accepts() -> None:
    car_ids = [uuid.uuid4() for _ in range(CARS_PER_NOTICE * 2 + 1)]

    notices = encode_car_changes(car_ids)

    assert len(notices) == 3
    assert all(len(notice.encode()) < 8000 for notice in notices)
    assert decode_notices(notices).car_ids == frozenset(car_ids)


def test_nothing_changed_needs_no_notice() -> None:
    assert encode_car_changes([]) == []


def test_notices_are_merged_and_repeats_collapse() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()

    notice = decode_notices(encode_car_changes([first]) + encode_car_changes([first, second]))

    assert notice.car_ids == {first, second}


@pytest.mark.parametrize("payload", ["[]", '{"cars": "x"}', '{"cars": ["not-a-uuid"]}', "{"])
def test_a_foreign_payload_is_an_error(payload: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - json, uuid and our own check all raise it
        decode_notices([payload])


def test_a_subscriber_that_falls_behind_is_dropped_alone(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> tuple[Subscription, Subscription]:
        monkeypatch.setattr(stream, "SUBSCRIBER_QUEUE_SIZE", 2)
        hub = FleetHub(make_settings(), async_sessionmaker(class_=AsyncSession))
        hub._listening.set()
        slow, fast = hub.subscribe(), hub.subscribe()
        assert slow is not None
        assert fast is not None
        for index in range(3):
            hub.publish(json.dumps({"n": index}))
            fast.queue.get_nowait()
        return slow, fast

    slow, fast = asyncio.run(run())

    assert slow.dropped.is_set()
    assert slow.close_code == stream.CLOSE_TRY_AGAIN
    assert not fast.dropped.is_set()


def test_nobody_subscribes_while_the_hub_does_not_listen(make_settings: SettingsFactory) -> None:
    async def run() -> Subscription | None:
        return FleetHub(make_settings(), async_sessionmaker(class_=AsyncSession)).subscribe()

    assert asyncio.run(run()) is None
