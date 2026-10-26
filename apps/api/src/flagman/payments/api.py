from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import Limit, RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.identity.dependencies import CurrentClient, get_current_client
from flagman.payments import service
from flagman.payments.fake_provider import WEBHOOK_PATH
from flagman.payments.ports import PaymentWebhook
from flagman.payments.schemas import (
    CardBindingIn,
    CardBindingOut,
    CardBindingStarted,
    PaymentCardList,
    PaymentCardOut,
)

# Each binding opens the provider's page and may cost the provider a verification; a client
# binds a card or two, so a dozen an hour is plenty and stops anyone probing cards through us.
BINDING_LIMIT = Limit("card-binding-user", 10, 3600)
# A webhook is a few hundred bytes of JSON.
MAX_WEBHOOK_BODY_BYTES = 16 * 1024

router = APIRouter(
    prefix="/payment-cards",
    tags=["payments"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: refresh or sign in.",
        }
    },
)

# The client comes first in every signature: without a token the answer is 401, not 503.
Client = Annotated[CurrentClient, Depends(get_current_client)]
Db = Annotated[AsyncSession, Depends(get_session)]

_CARD_NOT_FOUND: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:payment-card-not-found`.",
}


def get_gateway(resources: Annotated[Resources, Depends(get_resources)]) -> service.Gateway:
    return service.gateway(resources.settings)


Gateway = Annotated[service.Gateway, Depends(get_gateway)]


def _no_store(response: Response) -> None:
    # Card masks and binding outcomes are the client's own; no shared cache keeps them.
    response.headers["Cache-Control"] = "no-store"


@router.get("", summary="The client's cards, oldest first")
async def list_cards(client: Client, db: Db, response: Response) -> PaymentCardList:
    _no_store(response)
    cards = await service.list_cards(db, client.user_id)
    now = datetime.now(UTC)
    return PaymentCardList(items=[PaymentCardOut.of(card, now) for card in cards])


@router.post(
    "/bindings",
    summary="Start binding a card: open the provider's page it returns",
    description=(
        "The card number goes to the provider's page only. When the client is done, the page "
        "sends them to `return_url` with `binding_id`; the outcome comes from the provider "
        "separately, so ask `GET /payment-cards/bindings/{binding_id}` until it is not `pending`. "
        "The first card becomes the default one."
    ),
    status_code=HTTPStatus.CREATED,
    responses={
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": Problem,
            "description": "`urn:flagman:problem:return-url-not-allowed`, or `validation-error`.",
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": Problem,
            "description": "`urn:flagman:problem:payments-unavailable`.",
        },
    },
)
async def start_binding(  # noqa: PLR0913 - FastAPI dependencies
    *,
    client: Client,
    body: CardBindingIn,
    request: Request,
    gw: Gateway,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    resources: Annotated[Resources, Depends(get_resources)],
    db: Db,
) -> CardBindingStarted:
    await limiter.hit(BINDING_LIMIT, str(client.user_id))
    binding_request = service.BindingRequest(
        user_id=client.user_id,
        return_url=body.return_url,
        rules=service.return_url_rules(resources.settings),
        api_base_url=str(request.base_url),
    )
    binding, page_url = await service.start_binding(db, gw, binding_request, datetime.now(UTC))
    return CardBindingStarted(id=binding.id, page_url=page_url, expires_at=binding.expires_at)


@router.get(
    "/bindings/{binding_id}",
    summary="How a card binding ended, or that it is still pending",
    responses={
        HTTPStatus.NOT_FOUND: {
            "model": Problem,
            "description": "`urn:flagman:problem:card-binding-not-found`.",
        }
    },
)
async def get_binding(
    client: Client, binding_id: UUID, db: Db, response: Response
) -> CardBindingOut:
    _no_store(response)
    binding, card = await service.get_binding(db, client.user_id, binding_id)
    return CardBindingOut.of(binding, card, datetime.now(UTC))


@router.delete(
    "/{card_id}",
    summary="Remove a card",
    description="If it was the default one, the newest other card becomes the default.",
    status_code=HTTPStatus.NO_CONTENT,
    responses={HTTPStatus.NOT_FOUND: _CARD_NOT_FOUND},
)
async def remove_card(client: Client, card_id: UUID, db: Db) -> None:
    await service.remove_card(db, client.user_id, card_id, datetime.now(UTC))


@router.post(
    "/{card_id}/default",
    summary="Make a card the default one",
    responses={HTTPStatus.NOT_FOUND: _CARD_NOT_FOUND},
)
async def set_default_card(client: Client, card_id: UUID, db: Db) -> PaymentCardOut:
    card = await service.set_default_card(db, client.user_id, card_id)
    return PaymentCardOut.of(card, datetime.now(UTC))


webhook_router = APIRouter()


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BODY_BYTES:
        raise RequestValidationError([{"loc": ("body",), "msg": "too large", "type": "too_long"}])
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            raise RequestValidationError(
                [{"loc": ("body",), "msg": "too large", "type": "too_long"}]
            )
    return bytes(body)


def _target(request: Request) -> bytes:
    # The bytes the provider sent: a decoded and re-encoded path could differ from what was signed.
    path: bytes = request.scope.get("raw_path") or request.url.path.encode()
    query: bytes = request.scope.get("query_string", b"")
    return path + b"?" + query if query else path


# Called by the provider, not by our clients: kept out of the OpenAPI schema and the generated
# client. Answers 200 also to a repeated event, so the provider stops resending it.
@webhook_router.post(WEBHOOK_PATH, include_in_schema=False)
async def receive_webhook(request: Request, gw: Gateway, db: Db) -> dict[str, bool]:
    webhook = PaymentWebhook(
        method=request.method,
        target=_target(request),
        headers=request.headers,
        body=await _read_body(request),
    )
    try:
        applied = await service.receive_webhook(db, gw, webhook, datetime.now(UTC))
    except ValidationError as error:
        raise RequestValidationError(
            [{**item, "loc": ("body", *item["loc"])} for item in error.errors(include_input=False)]
        ) from error
    except ValueError as error:
        raise RequestValidationError(
            [{"loc": ("body",), "msg": "not a valid event", "type": "value_error"}]
        ) from error
    return {"applied": applied}
