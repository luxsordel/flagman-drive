import io

import pytest

from conftest import SettingsFactory
from flagman.pricing import cli


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
def test_seed_runs_again_without_changes(command_env: pytest.MonkeyPatch) -> None:
    assert _run("seed")[0] == cli.EXIT_OK

    assert _run("seed") == (cli.EXIT_OK, "Tariffs created: 0.\n")


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
