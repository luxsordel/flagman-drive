from http import HTTPStatus
from typing import Annotated, NoReturn

from fastapi import APIRouter, Cookie, HTTPException

from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.identity.schemas import (
    LogoutRequest,
    OtpRequest,
    OtpRequestAccepted,
    OtpVerifyRequest,
    RefreshRequest,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Set HttpOnly, Secure, SameSite=Strict, Path=/auth (see refresh_token_transport in schemas).
REFRESH_COOKIE = "flagman_refresh"
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


def _not_implemented() -> NoReturn:
    # The contract is published before the implementation (#31) so that the clients can start.
    raise HTTPException(HTTPStatus.NOT_IMPLEMENTED, "Sign-in is not implemented yet.")


@router.post(
    "/otp/request",
    summary="Send a one-time sign-in code by SMS",
    status_code=HTTPStatus.ACCEPTED,
    responses={HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE},
)
async def request_otp(body: OtpRequest) -> OtpRequestAccepted:
    _not_implemented()


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
async def verify_otp(body: OtpVerifyRequest) -> TokenPair:
    _not_implemented()


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
async def refresh_tokens(body: RefreshRequest, refresh_cookie: RefreshCookie = None) -> TokenPair:
    _not_implemented()


@router.post(
    "/logout",
    summary="End the session of this refresh token",
    description=(
        "Idempotent: an unknown or already revoked token also gives 204. "
        f"Clears the cookie. {_COOKIE_BODY_NOTE}"
    ),
    status_code=HTTPStatus.NO_CONTENT,
)
async def logout(body: LogoutRequest, refresh_cookie: RefreshCookie = None) -> None:
    _not_implemented()
