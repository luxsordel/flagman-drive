import base64

import pytest

from conftest import SettingsFactory
from flagman.payments import service

_PAYMENTS = {
    "payments_provider": "fake",
    "payments_webhook_secret": "w" * 32,
    "payments_token_encryption_key": base64.b64encode(b"p" * 32).decode(),
}


@pytest.mark.parametrize(("api_env", "allowed"), [("dev", True), ("test", True), ("stand", False)])
def test_expo_go_returns_only_off_the_internet(
    make_settings: SettingsFactory, api_env: str, allowed: bool
) -> None:
    # Expo Go links lead to any developer's laptop: a public stand must not send a card there.
    rules = service.return_url_rules(make_settings(**_PAYMENTS, api_env=api_env))

    assert rules.allow_expo_go is allowed
