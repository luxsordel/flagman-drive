from typing import Any, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    # Empty means the default: Compose passes every variable, set or not.
    model_config = SettingsConfigDict(env_prefix="EMULATOR_", extra="ignore", env_ignore_empty=True)

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

    # The fleet's background life between commands (car_emulator.domain.step_fleet).
    simulation_enabled: bool = True
    # Simulated seconds per real second: 60 turns an hour of drain and driving into a minute.
    time_scale: float = Field(default=1.0, gt=0, le=3600)
    trip_share: float = Field(default=0.1, ge=0, le=1)
    speed_kmh: float = Field(default=30.0, gt=0, le=150)
    idle_drain_pct_per_h: float = Field(default=1.0, ge=0, le=100)
    drive_drain_pct_per_km: float = Field(default=0.2, ge=0, le=100)
    # Above the API's FLEET_MIN_BATTERY_PCT by default, so cars charge before they leave the map.
    charge_below_pct: float = Field(default=25.0, ge=0, le=100)
    charge_until_pct: float = Field(default=90.0, ge=0, le=100)
    charge_rate_pct_per_h: float = Field(default=40.0, gt=0, le=1000)

    @field_validator("command_secret", "telemetry_secret")
    @classmethod
    def _secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MIN_SECRET_LENGTH:
            raise ValueError(f"must be at least {_MIN_SECRET_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _charging_ends_above_its_start(self) -> Self:
        # Otherwise a car would stop charging below the level that sends it to charge again.
        if self.charge_until_pct <= self.charge_below_pct:
            raise ValueError("charge_until_pct must be above charge_below_pct")
        return self

    @field_validator("telemetry_url", mode="before")
    @classmethod
    def _empty_url_is_none(cls, value: Any) -> Any:
        return None if value == "" else value
