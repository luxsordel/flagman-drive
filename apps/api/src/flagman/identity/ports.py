from http import HTTPStatus
from typing import Protocol

import structlog

from flagman.core.config import Settings
from flagman.core.errors import AppError

logger = structlog.get_logger(__name__)


class SignInUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "sign-in-unavailable"
    title = "Sign-in is unavailable"


class SmsSender(Protocol):
    async def send_otp(self, phone: str, code: str) -> None:
        """Delivers the sign-in code; the adapter owns the message text and its language."""
        ...


def mask_phone(phone: str) -> str:
    """`+375291234567` -> `+375 29 *** ** 67`: enough to tell test phones apart in the log."""
    return f"{phone[:4]} {phone[4:6]} *** ** {phone[-2:]}"


class LogSmsSender:
    """Pilot stub: the code goes to the API log instead of an SMS. Refused in prod by Settings."""

    async def send_otp(self, phone: str, code: str) -> None:
        logger.info("otp_sms_stub", phone=mask_phone(phone), code=code)


def create_sms_sender(settings: Settings) -> SmsSender:
    match settings.sms_backend:
        case "log":
            return LogSmsSender()
        case None:
            raise SignInUnavailableError("No SMS gateway is configured.")
