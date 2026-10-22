from http import HTTPStatus
from typing import Protocol

import structlog

from flagman.core.config import Settings
from flagman.core.errors import AppError
from flagman.identity.domain import generate_otp_code

logger = structlog.get_logger(__name__)


class SignInUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "sign-in-unavailable"
    title = "Sign-in is unavailable"


class SignInNotAllowedError(AppError):
    """The pilot has no SMS gateway and this phone is not one of its test phones."""

    status = HTTPStatus.FORBIDDEN
    slug = "sign-in-not-allowed"
    title = "Sign-in is not allowed for this phone"


class SmsSender(Protocol):
    def choose_code(self, phone: str) -> str:
        """The code for this phone; raises SignInNotAllowedError if the phone cannot get one."""
        ...

    async def send_otp(self, phone: str, code: str) -> None:
        """Delivers the sign-in code; the adapter owns the message text and its language."""
        ...


def mask_phone(phone: str) -> str:
    """`+375291234567` -> `+375 29 *** ** 67`: enough to tell test phones apart in the log."""
    return f"{phone[:4]} {phone[4:6]} *** ** {phone[-2:]}"


class LogSmsSender:
    """Development stub: the code goes to the API log instead of an SMS. Refused in prod."""

    def choose_code(self, phone: str) -> str:
        return generate_otp_code()

    async def send_otp(self, phone: str, code: str) -> None:
        logger.info("otp_sms_stub", phone=mask_phone(phone), code=code)


class FixedCodeSmsSender:
    """Pilot without an SMS gateway: the pre-registered test phones share one code.

    Nothing is sent and nothing is logged, so the API can run in prod mode. The code is known to
    everyone who demos the pilot; attempts and rate limits still apply as to any other code.
    """

    def __init__(self, code: str, phones: frozenset[str]) -> None:
        self._code = code
        self._phones = phones

    def choose_code(self, phone: str) -> str:
        if phone not in self._phones:
            raise SignInNotAllowedError
        return self._code

    async def send_otp(self, phone: str, code: str) -> None:
        """Nothing to send: the test users already know the code."""


def create_sms_sender(settings: Settings) -> SmsSender:
    match settings.sms_backend:
        case "log":
            return LogSmsSender()
        # Settings make sure the code is there; the guard only narrows the type.
        case "fixed" if settings.sms_fixed_code is not None:
            return FixedCodeSmsSender(
                settings.sms_fixed_code.get_secret_value(), frozenset(settings.sms_fixed_phones)
            )
        case _:
            raise SignInUnavailableError("No SMS gateway is configured.")
