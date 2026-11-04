import subprocess
from pathlib import Path

import pytest
import requests

from flagman_load import cli, stack
from flagman_load.bootstrap import BootstrapError
from flagman_load.cli import parse_env, stand_plan
from flagman_load.profiles import STAND

VALUES = {
    "LOAD_STAND_API_URL": "https://api.stand.example/",
    "LOAD_STAND_ADMIN_ORIGIN": "https://admin.stand.example",
    "LOAD_STAND_PHONES": ",".join(f"+37529100000{i}" for i in range(STAND.phones_needed)),
    "LOAD_STAND_OTP_CODE": "402918",
    "LOAD_STAND_STAFF_EMAIL": "operator@stand.example",
    "LOAD_STAND_STAFF_PASSWORD": "not-a-real-password",
    "LOAD_STAND_STAFF_TOTP_SECRET": "ABCDEFGH",
}


def test_parse_env_skips_comments_and_keeps_equal_signs_in_values() -> None:
    assert parse_env("# note\n\nA=1\n B = x=y \n") == {"A": "1", "B": "x=y"}


def test_stand_never_sends_a_client_ip() -> None:
    plan = stand_plan(VALUES)
    assert plan.target.send_client_ip is False
    assert plan.target.base_url == "https://api.stand.example"
    assert plan.ws_url == "wss://api.stand.example/admin/fleet/stream"


def test_stand_names_what_is_missing_without_values() -> None:
    with pytest.raises(BootstrapError) as error:
        stand_plan({**VALUES, "LOAD_STAND_OTP_CODE": ""})
    assert "LOAD_STAND_OTP_CODE" in str(error.value)
    assert "not-a-real-password" not in str(error.value)


def test_stand_needs_https() -> None:
    with pytest.raises(BootstrapError):
        stand_plan({**VALUES, "LOAD_STAND_API_URL": "http://api.stand.example"})


def test_stand_needs_enough_phones() -> None:
    with pytest.raises(BootstrapError):
        stand_plan({**VALUES, "LOAD_STAND_PHONES": "+375291000000"})


@pytest.mark.parametrize(("passed", "code"), [(True, 0), (False, 1)])
def test_exit_code_tells_met_from_missed(
    monkeypatch: pytest.MonkeyPatch, passed: bool, code: int
) -> None:
    monkeypatch.setattr(cli, "run_stand", lambda: passed)
    assert cli.main(["stand"]) == code


@pytest.mark.parametrize(
    "error",
    [
        BootstrapError("no approval"),
        requests.ConnectionError("the API is down"),
        FileNotFoundError("docker"),
        KeyError("items"),
        ValueError("not JSON"),
        TypeError("an older summary.json"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_run_that_could_not_happen_is_not_a_missed_target(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def stopped() -> bool:
        raise error

    monkeypatch.setattr(cli, "run_stand", stopped)
    assert cli.main(["stand"]) == 2


def test_a_new_stack_first_removes_what_an_earlier_run_left(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str], **_: object) -> None:
        commands.append(command)

    monkeypatch.setattr(subprocess, "run", record)
    stack.Stack(tmp_path / "stack.env").up()
    first = commands[0]
    assert first[first.index("down") :] == ["down", "--volumes", "--remove-orphans"]
    assert any("alembic" in command for command in commands[1:])


def test_only_services_with_a_healthcheck_are_waited_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def record(command: list[str], **_: object) -> None:
        commands.append(command)

    monkeypatch.setattr(subprocess, "run", record)
    stack.Stack(tmp_path / "stack.env").up()
    started = [command for command in commands if "up" in command]
    waited = next(command for command in started if "--wait" in command)
    assert {"api", "car-emulator"} <= set(waited)
    assert "worker" not in waited
    assert any("worker" in command for command in started)
