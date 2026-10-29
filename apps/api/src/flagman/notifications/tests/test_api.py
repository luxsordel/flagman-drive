from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flagman.notifications.models import PushDevice
from flagman.notifications.tests.conftest import Caller, push_token

pytestmark = pytest.mark.integration


async def _devices(sessions: async_sessionmaker[AsyncSession], caller: Caller) -> list[Any]:
    async with sessions() as db:
        rows = await db.scalars(select(PushDevice).where(PushDevice.user_id == caller.id))
        return [(row.session_id, row.token, row.platform) for row in rows]


def _register(client: TestClient, caller: Caller, token: str, platform: str = "ios") -> Any:
    return client.put(
        "/me/push-device", json={"token": token, "platform": platform}, headers=caller.headers
    )


async def test_device_is_registered_for_the_session_and_replaced_on_a_new_token(
    client: TestClient, caller: Caller, sessions: async_sessionmaker[AsyncSession]
) -> None:
    first, second = push_token(), push_token()

    assert _register(client, caller, first).status_code == HTTPStatus.NO_CONTENT
    assert _register(client, caller, second, "android").status_code == HTTPStatus.NO_CONTENT

    assert await _devices(sessions, caller) == [(caller.session_id, second, "android")]


async def test_a_token_moves_to_whoever_signed_in_on_the_phone_last(
    client: TestClient, caller: Caller, other: Caller, sessions: async_sessionmaker[AsyncSession]
) -> None:
    token = push_token()
    _register(client, caller, token)

    assert _register(client, other, token).status_code == HTTPStatus.NO_CONTENT

    assert await _devices(sessions, caller) == []
    assert await _devices(sessions, other) == [(other.session_id, token, "ios")]


async def test_forgetting_removes_only_the_callers_own_device(
    client: TestClient, caller: Caller, other: Caller, sessions: async_sessionmaker[AsyncSession]
) -> None:
    mine, theirs = push_token(), push_token()
    _register(client, caller, mine)
    _register(client, other, theirs)

    assert client.delete("/me/push-device", headers=other.headers).status_code == (
        HTTPStatus.NO_CONTENT
    )
    # Idempotent.
    assert client.delete("/me/push-device", headers=other.headers).status_code == (
        HTTPStatus.NO_CONTENT
    )

    assert await _devices(sessions, caller) == [(caller.session_id, mine, "ios")]
    assert await _devices(sessions, other) == []


@pytest.mark.parametrize(
    "body",
    [
        {"token": "not-an-expo-token", "platform": "ios"},
        {"token": "ExponentPushToken[abc]", "platform": "web"},
        {"token": "ExponentPushToken[abc]", "platform": "ios", "user_id": "x"},
    ],
)
def test_bad_device_bodies_are_rejected(
    client: TestClient, caller: Caller, body: dict[str, str]
) -> None:
    response = client.put("/me/push-device", json=body, headers=caller.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/me/push-device"),
        ("DELETE", "/me/push-device"),
        ("GET", "/me/notification-preferences"),
        ("PATCH", "/me/notification-preferences"),
    ],
)
def test_everything_needs_a_signed_in_client(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == HTTPStatus.UNAUTHORIZED


def _enabled(body: Any) -> dict[str, tuple[bool, bool]]:
    return {c["category"]: (c["enabled"], c["mandatory"]) for c in body["categories"]}


def test_everything_is_on_by_default(client: TestClient, caller: Caller) -> None:
    response = client.get("/me/notification-preferences", headers=caller.headers)

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Cache-Control"] == "no-store"
    assert _enabled(response.json()) == {
        "reservation": (True, False),
        "trip": (True, True),
        "payment": (True, True),
    }


def test_preferences_change_for_the_caller_only(
    client: TestClient, caller: Caller, other: Caller
) -> None:
    response = client.patch(
        "/me/notification-preferences",
        json={"categories": {"reservation": False}},
        headers=caller.headers,
    )

    assert response.status_code == HTTPStatus.OK
    assert _enabled(response.json())["reservation"] == (False, False)
    mine = client.get("/me/notification-preferences", headers=caller.headers).json()
    theirs = client.get("/me/notification-preferences", headers=other.headers).json()
    assert _enabled(mine)["reservation"] == (False, False)
    assert _enabled(theirs)["reservation"] == (True, False)


def test_mandatory_categories_cannot_be_switched_off(client: TestClient, caller: Caller) -> None:
    response = client.patch(
        "/me/notification-preferences",
        json={"categories": {"reservation": False, "payment": False}},
        headers=caller.headers,
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["type"] == "urn:flagman:problem:notification-category-mandatory"
    # Nothing of the request is applied.
    current = client.get("/me/notification-preferences", headers=caller.headers).json()
    assert _enabled(current)["reservation"] == (True, False)


@pytest.mark.parametrize(
    "body", [{"categories": {"news": False}}, {"categories": {}, "extra": 1}, {}]
)
def test_bad_preference_bodies_are_rejected(
    client: TestClient, caller: Caller, body: dict[str, Any]
) -> None:
    response = client.patch("/me/notification-preferences", json=body, headers=caller.headers)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
