import io

import pytest

from conftest import SettingsFactory
from flagman.fleet import cli


@pytest.fixture
def command_env(
    integration_env: dict[str, str], migrated_database: None, monkeypatch: pytest.MonkeyPatch
) -> pytest.MonkeyPatch:
    # The command reads the settings from the environment, like the API process does.
    for name, value in integration_env.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def _run(*argv: str) -> tuple[int, str]:
    stdout = io.StringIO()
    code = cli.main(list(argv), stdout=stdout)
    return code, stdout.getvalue()


@pytest.mark.integration
def test_seed_reads_the_car_count(command_env: pytest.MonkeyPatch) -> None:
    # Not the default of 30: the output proves the count came from the environment.
    command_env.setenv("EMULATOR_SEED", "42")
    command_env.setenv("EMULATOR_CAR_COUNT", "5")
    assert _run("seed")[0] == cli.EXIT_OK

    code, output = _run("seed")

    assert code == cli.EXIT_OK
    assert "Cars created: 0, already there: 5." in output


@pytest.mark.integration
def test_seed_reads_the_emulator_seed(
    command_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    command_env.setenv("EMULATOR_SEED", "42")
    assert _run("seed")[0] == cli.EXIT_OK
    # Another seed means other telematics ids for the same demo plates.
    command_env.setenv("EMULATOR_SEED", "43")

    code, output = _run("seed")

    assert code == cli.EXIT_FAILED
    assert output == ""
    assert "EMULATOR_SEED" in capsys.readouterr().err


def test_seed_is_refused_in_production(
    make_settings: SettingsFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No database behind it: the command must stop before connecting.
    monkeypatch.setattr(cli, "load_settings", lambda: make_settings(api_env="prod"))

    code, output = _run("seed")

    assert code == cli.EXIT_FAILED
    assert output == ""
    assert "API_ENV=prod" in capsys.readouterr().err
