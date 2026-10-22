from http import HTTPStatus

from flagman.core.errors import AppError


class OtpInvalidError(AppError):
    status = HTTPStatus.BAD_REQUEST
    slug = "otp-invalid"
    title = "Invalid code"


class OtpExpiredError(AppError):
    """The code expired, ran out of attempts or was never requested: the client asks for a new one.

    These cases are not told apart, so the response does not reveal whether a code was sent.
    """

    status = HTTPStatus.BAD_REQUEST
    slug = "otp-expired"
    title = "Code expired"


class RefreshTokenInvalidError(AppError):
    """Unknown, expired, revoked or reused refresh token: the client signs in again."""

    status = HTTPStatus.UNAUTHORIZED
    slug = "refresh-token-invalid"
    title = "Refresh token is invalid"
