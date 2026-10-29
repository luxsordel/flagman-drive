"""`flagman-staff ensure` before it reaches the database (#123)."""

import io

import pyotp
import pytest

from conftest import SettingsFactory
from flagman.identity import cli

PASSWORD = "correct horse battery staple"


def _ensure(*extra: str, stdin: str) -> tuple[int, str]:
    stdout = io.StringIO()
    argv = ["ensure", "--email", "operator@stand.test", "--role", "operator", *extra]
    code = cli.main(argv, stdin=io.StringIO(stdin), stdout=stdout)
    return code, stdout.getvalue()


@pytest.fixture
def stand(make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    # No database behind it: --check and the prod guard must stop before connecting.
    monkeypatch.setattr(cli, "load_settings", lambda: make_settings(api_env="stand"))


def test_ensure_is_refused_in_production(
    make_settings: SettingsFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: make_settings(api_env="prod"))

    code, output = _ensure(stdin=f"{PASSWORD}\n{pyotp.random_base32()}\n")

    assert code == cli.EXIT_FAILED
    assert output == ""
    assert "API_ENV=prod" in capsys.readouterr().err


def test_check_accepts_a_good_password_and_secret(stand: None) -> None:
    secret = pyotp.random_base32()

    code, output = _ensure("--check", stdin=f"{PASSWORD}\n{secret}\n")

    assert code == cli.EXIT_OK
    assert PASSWORD not in output
    assert secret not in output


@pytest.mark.parametrize(
    ("password", "secret"),
    [("short", pyotp.random_base32()), (PASSWORD, "JBSWY3DPEHPK3PXP"), (PASSWORD, "")],
)
def test_check_refuses_a_weak_password_or_2fa_secret(
    stand: None, password: str, secret: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _ensure("--check", stdin=f"{password}\n{secret}\n")

    assert code == cli.EXIT_FAILED
    assert password not in capsys.readouterr().err
