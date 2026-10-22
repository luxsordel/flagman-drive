from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.identity.dependencies import CurrentStaff, get_current_staff
from flagman.identity.domain import (
    REFRESH_TOKEN_TTL,
    RefreshTokenInvalidError,
)
from flagman.identity.ports import SmsSender, create_sms_sender
from flagman.identity.schemas import (
    LogoutRequest,
    OtpRequest,
    OtpRequestAccepted,
    OtpVerifyRequest,
    RefreshRequest,
    RefreshTokenTransport,
    StaffAccessToken,
    StaffChallengeIssued,
    StaffPasswordRequest,
    StaffProfile,
    StaffRefreshRequest,
    StaffTotpEnrollment,
    StaffTotpEnrollmentRequest,
    StaffTotpRequest,
    TokenPair,
)
from flagman.identity.service import SignInService, StaffSignInService, StaffTokens

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
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:sign-in-not-allowed`: the pilot runs without SMS "
                "and only its test phones can sign in."
            ),
        },
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
async def verify_otp(
    body: OtpVerifyRequest, request: Request, response: Response, service: Service
) -> TokenPair:
    tokens = await service.verify_otp(
        body.phone, body.code, body.device_id, _client_ip(request), datetime.now(UTC)
    )
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


# Staff sign-in (#32, docs/adr/0011). The admin panel is a web app only, so the refresh token
# always travels in a cookie of its own, apart from the client's and sent to the staff routes only.
staff_router = APIRouter(prefix="/staff", tags=["staff-auth"])

STAFF_REFRESH_COOKIE = "flagman_staff_refresh"
STAFF_REFRESH_COOKIE_PATH = "/staff/auth"
StaffRefreshCookie = Annotated[str | None, Cookie(alias=STAFF_REFRESH_COOKIE)]


def get_staff_sign_in_service(
    db: Annotated[AsyncSession, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> StaffSignInService:
    return StaffSignInService(db=db, limiter=limiter, settings=resources.settings)


StaffService = Annotated[StaffSignInService, Depends(get_staff_sign_in_service)]
Origin = Annotated[RequestOrigin, Depends(get_request_origin)]


def _no_store(response: Response) -> None:
    # Tokens and the TOTP secret must not stay in a browser or proxy cache.
    response.headers["Cache-Control"] = "no-store"


def _deliver_staff(tokens: StaffTokens, response: Response, now: datetime) -> StaffAccessToken:
    response.set_cookie(
        STAFF_REFRESH_COOKIE,
        tokens.refresh_token,
        # Until the session ends, not a full TTL from now: refreshes do not extend the session.
        max_age=max(int((tokens.session_expires_at - now).total_seconds()), 1),
        path=STAFF_REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return tokens.access_token


_CHALLENGE_INVALID = {
    "model": Problem,
    "description": "`urn:flagman:problem:staff-challenge-invalid`: start again with the password.",
}


@staff_router.post(
    "/auth/password",
    summary="Staff sign-in, step 1: check the email and password",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:staff-credentials-invalid`: wrong email or password, "
                "or the account is disabled; the cases are not told apart."
            ),
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def staff_check_password(
    body: StaffPasswordRequest, origin: Origin, service: StaffService
) -> StaffChallengeIssued:
    return await service.check_password(body.email, body.password, origin, datetime.now(UTC))


@staff_router.post(
    "/auth/totp/enrollment",
    summary="Staff sign-in, first time only: get a new TOTP secret for the QR code",
    description=(
        "Each call replaces the secret that is not confirmed yet. "
        "The first accepted code in `POST /staff/auth/totp` finishes the setup."
    ),
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.UNAUTHORIZED: _CHALLENGE_INVALID,
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:totp-already-enrolled`: only an administrator resets 2FA."
            ),
        },
    },
)
async def staff_start_totp_enrollment(
    body: StaffTotpEnrollmentRequest, service: StaffService
) -> StaffTotpEnrollment:
    return await service.start_totp_enrollment(body.challenge_token)


@staff_router.post(
    "/auth/totp",
    summary="Staff sign-in, step 2: check the TOTP code and start a session",
    description="The refresh token is set as an HttpOnly cookie and is not in the body.",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.BAD_REQUEST: {
            "model": Problem,
            "description": "`urn:flagman:problem:totp-invalid`: wrong code, the user may retry.",
        },
        HTTPStatus.UNAUTHORIZED: _CHALLENGE_INVALID,
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:totp-enrollment-not-started`: get the QR code first. "
                "`urn:flagman:problem:totp-reset-required`: an administrator must reset 2FA."
            ),
        },
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
    },
)
async def staff_verify_totp(
    body: StaffTotpRequest, origin: Origin, response: Response, service: StaffService
) -> StaffAccessToken:
    now = datetime.now(UTC)
    tokens = await service.verify_totp(body.challenge_token, body.code, origin, now)
    return _deliver_staff(tokens, response, now)


@staff_router.post(
    "/auth/token/refresh",
    summary="Rotate the staff refresh cookie and get a new access token",
    description=f"The new token carries the current role. {_COOKIE_BODY_NOTE}",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:refresh-token-invalid`: sign in again.",
        },
    },
)
async def staff_refresh_tokens(
    body: StaffRefreshRequest,
    response: Response,
    service: StaffService,
    refresh_cookie: StaffRefreshCookie = None,
) -> StaffAccessToken:
    if refresh_cookie is None:
        raise RefreshTokenInvalidError
    now = datetime.now(UTC)
    tokens = await service.refresh_tokens(refresh_cookie, now)
    return _deliver_staff(tokens, response, now)


@staff_router.post(
    "/auth/logout",
    summary="End the staff session of the refresh cookie",
    description=f"Idempotent: without a valid cookie it also gives 204. {_COOKIE_BODY_NOTE}",
    status_code=HTTPStatus.NO_CONTENT,
)
async def staff_logout(
    body: StaffRefreshRequest,
    origin: Origin,
    response: Response,
    service: StaffService,
    refresh_cookie: StaffRefreshCookie = None,
) -> None:
    if refresh_cookie is not None:
        await service.logout(refresh_cookie, origin, datetime.now(UTC))
    response.delete_cookie(
        STAFF_REFRESH_COOKIE,
        path=STAFF_REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


@staff_router.get(
    "/me",
    summary="The signed-in staff member",
    description="The admin panel builds its menu from `role` after a page reload.",
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: refresh or sign in.",
        },
    },
)
async def staff_me(
    staff: Annotated[CurrentStaff, Depends(get_current_staff)], service: StaffService
) -> StaffProfile:
    return await service.get_profile(staff.staff_id)
