from typing import Any

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMULATOR_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8100, ge=1, le=65535)

    car_count: int = Field(default=30, ge=1, le=1000)
    seed: int = 42

    # Separate secrets: leaking the telemetry one must not allow sending commands to cars.
    command_secret: SecretStr
    telemetry_secret: SecretStr
    signature_tolerance_s: int = Field(default=300, ge=1, le=3600)

    # Empty means "no API to send to": telemetry goes to the log stub instead of a webhook.
    telemetry_url: HttpUrl | None = None
    telemetry_interval_s: float = Field(default=5.0, gt=0)

    @field_validator("command_secret", "telemetry_secret")
    @classmethod
    def _secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MIN_SECRET_LENGTH:
            raise ValueError(f"must be at least {_MIN_SECRET_LENGTH} characters")
        return value

    @field_validator("telemetry_url", mode="before")
    @classmethod
    def _empty_url_is_none(cls, value: Any) -> Any:
        return None if value == "" else value
