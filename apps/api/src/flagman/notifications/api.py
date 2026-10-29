from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import Limit, RateLimiter, get_rate_limiter
from flagman.identity.dependencies import CurrentClient, get_current_client
from flagman.notifications import service
from flagman.notifications.schemas import (
    NotificationPreferencesIn,
    NotificationPreferencesOut,
    PushDeviceIn,
)

# The app registers its token after each sign-in and when the OS hands it a new one: a handful
# a day. The limit only keeps one account from churning through tokens.
DEVICE_LIMIT = Limit("push-device-user", 30, 3600)

router = APIRouter(
    prefix="/me",
    tags=["notifications"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: refresh or sign in.",
        }
    },
)

# The client comes first in every signature: without a token the answer is 401.
Client = Annotated[CurrentClient, Depends(get_current_client)]
Db = Annotated[AsyncSession, Depends(get_session)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.put(
    "/push-device",
    summary="Send pushes to this phone while this session lasts",
    description=(
        "One device per session: a second call replaces the token. A token registered by "
        "another session moves to this one. Signing out stops the pushes."
    ),
    status_code=HTTPStatus.NO_CONTENT,
    responses={HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE},
)
async def register_push_device(
    client: Client,
    body: PushDeviceIn,
    db: Db,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    await limiter.hit(DEVICE_LIMIT, str(client.user_id))
    await service.register_device(
        db,
        user_id=client.user_id,
        session_id=client.session_id,
        token=body.token,
        platform=body.platform,
        now=datetime.now(UTC),
    )


@router.delete(
    "/push-device",
    summary="Stop pushes to this phone",
    description="Idempotent: a session without a device also gives 204.",
    status_code=HTTPStatus.NO_CONTENT,
)
async def forget_push_device(client: Client, db: Db) -> None:
    await service.forget_device(db, client.user_id, client.session_id)


@router.get(
    "/notification-preferences",
    summary="Which notifications the client gets",
    dependencies=[Depends(_no_store)],
)
async def get_notification_preferences(client: Client, db: Db) -> NotificationPreferencesOut:
    return NotificationPreferencesOut.of(await service.get_preferences(db, client.user_id))


@router.patch(
    "/notification-preferences",
    summary="Switch categories of notifications on or off",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": Problem,
            "description": "`urn:flagman:problem:notification-category-mandatory`, "
            "or `validation-error`.",
        },
    },
)
async def update_notification_preferences(
    client: Client, body: NotificationPreferencesIn, db: Db
) -> NotificationPreferencesOut:
    preferences = await service.update_preferences(
        db, client.user_id, body.categories, datetime.now(UTC)
    )
    return NotificationPreferencesOut.of(preferences)
