from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.identity.domain import REFRESH_TOKEN_TTL, RefreshTokenInvalidError
from flagman.identity.ports import SmsSender, create_sms_sender
from flagman.identity.schemas import (
    LogoutRequest,
    OtpRequest,
    OtpRequestAccepted,
    OtpVerifyRequest,
    RefreshRequest,
    RefreshTokenTransport,
    TokenPair,
)
from flagman.identity.service import SignInService

router = APIRouter(prefix="/auth", tags=["auth"])

# See refresh_token_transport in schemas: the web client gets the refresh token in this cookie.
REFRESH_COOKIE = "flagman_refresh"
REFRESH_COOKIE_PATH = "/auth"
RefreshCookie = Annotated[
    str | None,
    Cookie(
        alias=REFRESH_COOKIE,
        description="Web client only; used when the body has no `refresh_token`.",
    ),
]
# The body stays required even when the token is in the cookie: a cross-site HTML form
# cannot send JSON, so it cannot make the browser refresh or end a session.
_COOKIE_BODY_NOTE = "The web client sends `{}` as the body and the refresh token in the cookie."


def get_sms_sender(resources: Annotated[Resources, Depends(get_resources)]) -> SmsSender:
    return create_sms_sender(resources.settings)


def get_sign_in_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> SignInService:
    return SignInService(db=db, limiter=limiter, settings=resources.settings)


Service = Annotated[SignInService, Depends(get_sign_in_service)]
# Only code requests need SMS: without a gateway, refresh and logout must keep working.
Sms = Annotated[SmsSender, Depends(get_sms_sender)]


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy this is the proxy unless uvicorn trusts it (--forwarded-allow-ips).
    return request.client.host if request.client else "unknown"


def _deliver(tokens: TokenPair, transport: RefreshTokenTransport, response: Response) -> TokenPair:
    if transport == "body" or tokens.refresh_token is None:
        return tokens
    # Page scripts cannot read it, other sites cannot send it, and it only goes to /auth.
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=int(REFRESH_TOKEN_TTL.total_seconds()),
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return tokens.model_copy(update={"refresh_token": None})


def _transport(body_token: str | None) -> RefreshTokenTransport:
    return "body" if body_token is not None else "cookie"


@router.post(
    "/otp/request",
    summary="Send a one-time sign-in code by SMS",
    status_code=HTTPStatus.ACCEPTED,
    responses={
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": Problem,
            "description": "`urn:flagman:problem:sign-in-unavailable`: no SMS gateway configured.",
        },
    },
)
async def request_otp(
    body: OtpRequest, request: Request, service: Service, sms: Sms
) -> OtpRequestAccepted:
    return await service.request_otp(sms, body.phone, _client_ip(request), datetime.now(UTC))


@router.post(
    "/otp/verify",
    summary="Exchange the code for tokens; creates the user on first sign-in",
    responses={
        HTTPStatus.BAD_REQUEST: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:otp-invalid`: wrong code, the user may retry. "
                "`urn:flagman:problem:otp-expired`: request a new code."
            ),
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def verify_otp(body: OtpVerifyRequest, response: Response, service: Service) -> TokenPair:
    tokens = await service.verify_otp(body.phone, body.code, body.device_id, datetime.now(UTC))
    return _deliver(tokens, body.refresh_token_transport, response)


@router.post(
    "/token/refresh",
    summary="Rotate the refresh token and get a new access token",
    description=f"The new refresh token comes back the way the old one came. {_COOKIE_BODY_NOTE}",
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:refresh-token-invalid`: sign in again.",
        },
    },
)
async def refresh_tokens(
    body: RefreshRequest, response: Response, service: Service, refresh_cookie: RefreshCookie = None
) -> TokenPair:
    refresh_token = body.refresh_token or refresh_cookie
    if refresh_token is None:
        raise RefreshTokenInvalidError
    tokens = await service.refresh_tokens(refresh_token, datetime.now(UTC))
    return _deliver(tokens, _transport(body.refresh_token), response)


@router.post(
    "/logout",
    summary="End the session of this refresh token",
    description=(
        "Idempotent: an unknown or already revoked token also gives 204. "
        f"Clears the cookie. {_COOKIE_BODY_NOTE}"
    ),
    status_code=HTTPStatus.NO_CONTENT,
)
async def logout(
    body: LogoutRequest, response: Response, service: Service, refresh_cookie: RefreshCookie = None
) -> None:
    refresh_token = body.refresh_token or refresh_cookie
    if refresh_token is not None:
        await service.logout(refresh_token, datetime.now(UTC))
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict"
    )
