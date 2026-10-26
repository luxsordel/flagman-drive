import pytest
from pydantic import ValidationError

from car_emulator.config import Settings
from tests.conftest import COMMAND_SECRET, TELEMETRY_SECRET


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_COMMAND_SECRET", COMMAND_SECRET)
    monkeypatch.setenv("EMULATOR_TELEMETRY_SECRET", TELEMETRY_SECRET)
    monkeypatch.setenv("EMULATOR_TELEMETRY_URL", "")
    monkeypatch.setenv("EMULATOR_CAR_COUNT", "12")
    settings = Settings()
    assert settings.command_secret.get_secret_value() == COMMAND_SECRET
    assert settings.telemetry_url is None
    assert settings.car_count == 12


def test_secrets_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMULATOR_COMMAND_SECRET", raising=False)
    monkeypatch.delenv("EMULATOR_TELEMETRY_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_short_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_COMMAND_SECRET", "short")
    monkeypatch.setenv("EMULATOR_TELEMETRY_SECRET", TELEMETRY_SECRET)
    with pytest.raises(ValidationError):
        Settings()


def test_secret_is_hidden_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_COMMAND_SECRET", COMMAND_SECRET)
    monkeypatch.setenv("EMULATOR_TELEMETRY_SECRET", TELEMETRY_SECRET)
    assert COMMAND_SECRET not in repr(Settings())


def test_simulation_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_COMMAND_SECRET", COMMAND_SECRET)
    monkeypatch.setenv("EMULATOR_TELEMETRY_SECRET", TELEMETRY_SECRET)
    monkeypatch.setenv("EMULATOR_TIME_SCALE", "60")
    settings = Settings()
    assert settings.simulation_enabled
    assert settings.time_scale == 60
    assert settings.charge_below_pct < settings.charge_until_pct


def test_charging_must_end_above_its_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMULATOR_COMMAND_SECRET", COMMAND_SECRET)
    monkeypatch.setenv("EMULATOR_TELEMETRY_SECRET", TELEMETRY_SECRET)
    monkeypatch.setenv("EMULATOR_CHARGE_BELOW_PCT", "50")
    monkeypatch.setenv("EMULATOR_CHARGE_UNTIL_PCT", "50")
    with pytest.raises(ValidationError):
        Settings()
