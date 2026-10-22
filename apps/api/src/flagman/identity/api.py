from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.identity.ports import SmsSender, create_sms_sender
from flagman.identity.schemas import (
    LogoutRequest,
    OtpRequest,
    OtpRequestAccepted,
    OtpVerifyRequest,
    RefreshRequest,
    TokenPair,
)
from flagman.identity.service import SignInService

router = APIRouter(prefix="/auth", tags=["auth"])


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
async def verify_otp(body: OtpVerifyRequest, service: Service) -> TokenPair:
    return await service.verify_otp(body.phone, body.code, body.device_id, datetime.now(UTC))


@router.post(
    "/token/refresh",
    summary="Rotate the refresh token and get a new access token",
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:refresh-token-invalid`: sign in again.",
        },
    },
)
async def refresh_tokens(body: RefreshRequest, service: Service) -> TokenPair:
    return await service.refresh_tokens(body.refresh_token, datetime.now(UTC))


@router.post(
    "/logout",
    summary="End the session of this refresh token",
    description="Idempotent: an unknown or already revoked token also gives 204.",
    status_code=HTTPStatus.NO_CONTENT,
)
async def logout(body: LogoutRequest, service: Service) -> None:
    await service.logout(body.refresh_token, datetime.now(UTC))
