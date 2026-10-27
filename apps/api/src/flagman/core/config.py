import base64
import binascii
import re
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

Environment = Literal["dev", "test", "prod"]

TOTP_KEY_BYTES = 32  # AES-256-GCM
PAYMENTS_TOKEN_KEY_BYTES = 32  # AES-256-GCM
MIN_SECRET_LENGTH = 32

# Same rules as the API accepts (flagman.identity.schemas), so every listed phone can sign in.
# [0-9] rather than \d: \d also matches non-ASCII digits.
PILOT_PHONE_RE = re.compile(r"\+375(25|29|33|44)[0-9]{7}")
OTP_CODE_RE = re.compile(r"[0-9]{6}")
_DIGIT_RUNS = "0123456789"


def _decode_key(key: SecretStr) -> bytes:
    try:
        return base64.b64decode(key.get_secret_value(), validate=True)
    except binascii.Error:
        return b""


class DatabaseSettings(BaseSettings):
    """What every process that talks to the database needs: the API and the worker."""

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

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str

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


class WorkerSettings(DatabaseSettings):
    """`flagman-worker` gets none of the API secrets: it does not sign or check anything."""

    # Ended sessions and their refresh tokens are kept this long before the periodic cleanup
    # deletes them (#46). Not a security knob: a replayed token of an ended session revokes
    # nothing either way (docs/adr/0009).
    auth_cleanup_retention_days: int = Field(default=7, ge=1)

    # Push notifications, see docs/adr/0020. "expo" sends through Expo Push Service, "fake" only
    # logs what would go out. Unset: notifications stay queued until a provider is configured.
    notifications_provider: Literal["expo", "fake"] | None = None
    # Only when "enhanced push security" is on for the Expo project.
    expo_access_token: SecretStr | None = None
    expo_push_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # How soon a queued notification goes out: a reminder about a reservation must not be late.
    notifications_dispatch_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    # Sent and undelivered notifications are kept this long, for support to see what went out.
    notifications_retention_days: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _validate_notifications(self) -> Self:
        if self.is_prod and self.notifications_provider == "fake":
            # Clients would silently get nothing.
            raise ValueError("NOTIFICATIONS_PROVIDER=fake is not allowed when API_ENV=prod")
        return self


class Settings(DatabaseSettings):
    api_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

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

    # Private bucket for client documents, see docs/adr/0005 and docs/adr/0012. The API talks to
    # the endpoint, while signed links point to the public URL: the S3 signature covers the host,
    # so a link signed for the internal address does not open from a phone.
    # Without the keys the API runs, but document uploads answer 503.
    s3_endpoint_url: str = "http://localhost:8333"
    s3_public_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = "flagman-private"

    # Admission thresholds the operator's approval is checked against (docs/01-vision-and-mvp.md).
    # The age floor guards against a typo that would admit minors.
    kyc_min_age_years: int = Field(default=21, ge=18)
    kyc_min_driving_years: int = Field(default=2, ge=0)

    # When a car is offered for rent (flagman.fleet.domain.is_rentable): enough charge to get
    # somewhere, and telemetry recent enough that the car is where the map shows it.
    fleet_min_battery_pct: int = Field(default=20, ge=0, le=100)
    fleet_telemetry_max_age_seconds: int = Field(default=300, gt=0)

    # Read by the demo seed (`flagman-fleet seed`) to derive the emulator's car ids; the names
    # are the emulator's own, so one .env drives both.
    emulator_seed: int = 42
    emulator_car_count: int = Field(default=30, ge=1, le=1000)
    # Checks the emulator's telemetry webhooks (docs/adr/0007), the same name as the emulator's.
    # Unset: the API runs, but telemetry answers 503 and no car becomes rentable.
    emulator_telemetry_secret: SecretStr | None = None
    # Car commands go to the emulator at this URL, signed with its own secret: a leaked webhook
    # secret must not move cars. Unset: the API runs, but commands answer 503.
    emulator_url: str | None = None
    emulator_command_secret: SecretStr | None = None
    # A command waits this long for the car; then the staff member is told the outcome is unknown.
    telematics_command_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # Clock skew allowed between the API and telematics, for signatures and snapshot times alike.
    telematics_signature_tolerance_seconds: int = Field(default=300, ge=1, le=3600)

    # Card payments, see docs/adr/0019. "fake" is the test provider inside the API: its own card
    # page and test card numbers, no money moves. Unset: the API runs, but cards cannot be bound
    # and payments answer 503.
    payments_provider: Literal["fake"] | None = None
    # Checks the provider's webhooks (HMAC + timestamp); the fake provider signs with it too.
    payments_webhook_secret: SecretStr | None = None
    payments_webhook_tolerance_seconds: int = Field(default=300, ge=1, le=3600)
    # Encrypts the provider's card tokens in the database, so a dump alone cannot charge a card.
    # 32 bytes in standard base64, like API_TOTP_ENCRYPTION_KEY but a different one.
    payments_token_encryption_key: SecretStr | None = None

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

    @field_validator("emulator_telemetry_secret", "emulator_command_secret")
    @classmethod
    def _validate_emulator_secret(
        cls, secret: SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        if secret is not None and len(secret.get_secret_value()) < MIN_SECRET_LENGTH:
            name = (info.field_name or "").upper()
            raise ValueError(f"{name} must be at least {MIN_SECRET_LENGTH} chars")
        return secret

    @field_validator("payments_webhook_secret")
    @classmethod
    def _validate_payments_webhook_secret(cls, secret: SecretStr | None) -> SecretStr | None:
        if secret is not None and len(secret.get_secret_value()) < MIN_SECRET_LENGTH:
            raise ValueError(f"PAYMENTS_WEBHOOK_SECRET must be at least {MIN_SECRET_LENGTH} chars")
        return secret

    @field_validator("payments_token_encryption_key")
    @classmethod
    def _validate_payments_token_key(cls, key: SecretStr | None) -> SecretStr | None:
        if key is not None and len(_decode_key(key)) != PAYMENTS_TOKEN_KEY_BYTES:
            raise ValueError(
                f"PAYMENTS_TOKEN_ENCRYPTION_KEY must be {PAYMENTS_TOKEN_KEY_BYTES} bytes in base64 "
                "(openssl rand -base64 32)"
            )
        return key

    @model_validator(mode="after")
    def _validate_payments(self) -> Self:
        if self.payments_provider is None:
            return self
        if self.is_prod and self.payments_provider == "fake":
            # Any test card would bind and every hold would pass without money.
            raise ValueError("PAYMENTS_PROVIDER=fake is not allowed when API_ENV=prod")
        if self.payments_webhook_secret is None or self.payments_token_encryption_key is None:
            raise ValueError(
                "PAYMENTS_PROVIDER needs PAYMENTS_WEBHOOK_SECRET and PAYMENTS_TOKEN_ENCRYPTION_KEY"
            )
        if self.payments_token_encryption_key.get_secret_value() == (
            self.api_totp_encryption_key.get_secret_value()
        ):
            raise ValueError(
                "PAYMENTS_TOKEN_ENCRYPTION_KEY and API_TOTP_ENCRYPTION_KEY must be different"
            )
        return self

    @field_validator("emulator_url")
    @classmethod
    def _validate_emulator_url(cls, url: str | None) -> str | None:
        if url is None:
            return url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"EMULATOR_URL must be scheme://host[:port], got {url!r}")
        if parts.query or parts.fragment:
            raise ValueError(f"EMULATOR_URL must not have a query, got {url!r}")
        return url.rstrip("/")

    @model_validator(mode="after")
    def _validate_secrets_differ(self) -> Self:
        telemetry, command = self.emulator_telemetry_secret, self.emulator_command_secret
        if telemetry and command and telemetry.get_secret_value() == command.get_secret_value():
            raise ValueError("EMULATOR_TELEMETRY_SECRET and EMULATOR_COMMAND_SECRET must differ")
        return self

    @field_validator("s3_endpoint_url", "s3_public_url")
    @classmethod
    def _validate_s3_url(cls, url: str | None) -> str | None:
        if url is None:
            return url
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"S3 URL must be scheme://host[:port], got {url!r}")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError(f"S3 URL must not have a path, got {url!r}")
        return url.rstrip("/")

    @model_validator(mode="after")
    def _validate_storage(self) -> Self:
        if (self.s3_access_key is None) != (self.s3_secret_key is None):
            raise ValueError("S3_ACCESS_KEY and S3_SECRET_KEY are set together or not at all")
        # Signed links carry document photos; over plain http anyone on the way could save them.
        if (
            self.is_prod
            and self.storage_configured
            and not self.storage_public_url.startswith("https://")
        ):
            raise ValueError("S3_PUBLIC_URL must be https when API_ENV=prod")
        return self

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
    def payments_token_key(self) -> bytes | None:
        key = self.payments_token_encryption_key
        return _decode_key(key) if key is not None else None

    @property
    def storage_configured(self) -> bool:
        return self.s3_access_key is not None and self.s3_secret_key is not None

    @property
    def storage_public_url(self) -> str:
        return self.s3_public_url or self.s3_endpoint_url


def load_settings() -> Settings:
    return Settings()


def load_worker_settings() -> WorkerSettings:
    return WorkerSettings()
