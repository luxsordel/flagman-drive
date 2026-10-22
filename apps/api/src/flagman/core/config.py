import base64
import binascii
import re
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

Environment = Literal["dev", "test", "prod"]

TOTP_KEY_BYTES = 32  # AES-256-GCM

# Same rules as the API accepts (flagman.identity.schemas), so every listed phone can sign in.
# [0-9] rather than \d: \d also matches non-ASCII digits.
PILOT_PHONE_RE = re.compile(r"\+375(25|29|33|44)[0-9]{7}")
OTP_CODE_RE = re.compile(r"[0-9]{6}")
_DIGIT_RUNS = "0123456789"


class Settings(BaseSettings):
    # The variable names are shared with infra/docker-compose.yml, so there is no prefix
    # for Postgres and Redis. The .env paths are relative to apps/api, where the API is run from.
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        # A failed check would otherwise print part of the input, which may end with a secret.
        hide_input_in_errors=True,
    )

    api_env: Environment = "dev"
    api_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr
    # For every call of the process-wide client; /ready has its own, shorter limit.
    redis_timeout_seconds: float = Field(default=5.0, gt=0)

    readiness_timeout_seconds: float = Field(default=2.0, gt=0)

    # Client sign-in, see docs/adr/0009. The pepper keeps OTP hashes and rate limit keys
    # unreversible even with the database dump: there are only a million codes per phone.
    api_jwt_secret: Annotated[SecretStr, Field(min_length=32)]
    api_otp_pepper: Annotated[SecretStr, Field(min_length=32)]
    # There is no SMS gateway before the end of the pilot (docs/adr/0009):
    # "log" writes codes to the log, for development only;
    # "fixed" lets the pre-registered test phones sign in with one shared code, nothing is logged.
    # Unset: the API runs, but nobody can request a sign-in code.
    sms_backend: Literal["log", "fixed"] | None = None
    sms_fixed_code: SecretStr | None = None
    sms_fixed_phones: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Staff sign-in, see docs/adr/0011. TOTP secrets are encrypted with this key, so a database
    # dump alone does not let anyone generate staff 2FA codes. 32 bytes in standard base64.
    api_totp_encryption_key: SecretStr

    @field_validator("api_cors_origins", "sms_fixed_phones", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("api_cors_origins")
    @classmethod
    def _validate_origins(cls, origins: list[str]) -> list[str]:
        # A wildcard would also be combined with credentials later (refresh cookie for the web),
        # so only explicit origins are accepted.
        for origin in origins:
            parts = urlsplit(origin)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"CORS origin must be scheme://host[:port], got {origin!r}")
            if parts.path or parts.query or parts.fragment:
                raise ValueError(f"CORS origin must not have a path, got {origin!r}")
        return origins

    @field_validator("api_totp_encryption_key")
    @classmethod
    def _validate_totp_key(cls, key: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(key.get_secret_value(), validate=True)
        except binascii.Error:
            raw = b""
        # The value itself stays out of the message: it is a secret.
        if len(raw) != TOTP_KEY_BYTES:
            raise ValueError(
                f"API_TOTP_ENCRYPTION_KEY must be {TOTP_KEY_BYTES} bytes in base64 "
                "(openssl rand -base64 32)"
            )
        return key

    @model_validator(mode="after")
    def _validate_secrets(self) -> Self:
        if self.api_jwt_secret.get_secret_value() == self.api_otp_pepper.get_secret_value():
            raise ValueError("API_JWT_SECRET and API_OTP_PEPPER must be different")
        if self.is_prod and self.sms_backend == "log":
            # Codes in the log would let anyone with log access sign in as any client.
            raise ValueError("SMS_BACKEND=log is not allowed when API_ENV=prod")
        return self

    @model_validator(mode="after")
    def _validate_fixed_sms(self) -> Self:
        if self.sms_backend != "fixed":
            return self
        # Messages never quote the values: the code is a secret and the phones are personal data.
        code = self.sms_fixed_code.get_secret_value() if self.sms_fixed_code else ""
        if not OTP_CODE_RE.fullmatch(code):
            raise ValueError("SMS_BACKEND=fixed needs SMS_FIXED_CODE of 6 digits")
        # The test phones are known to the whole team: a guessable code opens all of them at once.
        if len(set(code)) == 1 or code in _DIGIT_RUNS or code in _DIGIT_RUNS[::-1]:
            raise ValueError("SMS_FIXED_CODE is too easy to guess, generate a random one")
        if not self.sms_fixed_phones:
            raise ValueError("SMS_BACKEND=fixed needs at least one phone in SMS_FIXED_PHONES")
        if not all(PILOT_PHONE_RE.fullmatch(phone) for phone in self.sms_fixed_phones):
            raise ValueError(
                "SMS_FIXED_PHONES must be Belarusian mobile numbers in E.164, e.g. +375291234567"
            )
        return self

    @property
    def totp_encryption_key(self) -> bytes:
        return base64.b64decode(self.api_totp_encryption_key.get_secret_value())

    @property
    def is_prod(self) -> bool:
        return self.api_env == "prod"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


def load_settings() -> Settings:
    return Settings()
