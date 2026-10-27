import base64
from typing import Any

import pytest
from pydantic import ValidationError

from conftest import SettingsFactory
from flagman.core.config import WorkerSettings


def test_cors_origins_are_parsed_from_a_comma_separated_env_var(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://localhost:8081, https://admin.example.com ,")

    settings = make_settings()

    assert settings.api_cors_origins == ["http://localhost:8081", "https://admin.example.com"]


def test_cors_origins_default_to_none_allowed(make_settings: SettingsFactory) -> None:
    assert make_settings().api_cors_origins == []


@pytest.mark.parametrize(
    "origin",
    ["*", "localhost:8081", "ftp://example.com", "https://example.com/app", "https://"],
)
def test_cors_rejects_anything_but_explicit_origins(
    make_settings: SettingsFactory, origin: str
) -> None:
    with pytest.raises(ValidationError):
        make_settings(api_cors_origins=[origin])


def test_empty_env_values_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    # .env.example ships with empty values; they must not become empty host names.
    monkeypatch.setenv("POSTGRES_HOST", "")

    assert make_settings().postgres_host == "localhost"


def test_database_url_escapes_the_password(make_settings: SettingsFactory) -> None:
    settings = make_settings(postgres_password="p@ss:w/rd", postgres_host="db", postgres_port=6543)

    url = settings.database_url

    assert url.password == "p@ss:w/rd"
    assert url.render_as_string(hide_password=False).startswith(
        "postgresql+asyncpg://test:p%40ss%3Aw%2Frd@db:6543/"
    )


def test_secrets_are_not_shown_in_repr(make_settings: SettingsFactory) -> None:
    settings = make_settings(postgres_password="db-secret", redis_password="redis-secret")

    assert "db-secret" not in repr(settings)
    assert "redis-secret" not in repr(settings)
    assert "db-secret" not in repr(settings.database_url)


@pytest.mark.parametrize("field", ["api_jwt_secret", "api_otp_pepper"])
def test_sign_in_secrets_must_be_long(make_settings: SettingsFactory, field: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: "x" * 31})


def test_sign_in_secrets_must_differ(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="must be different"):
        make_settings(api_jwt_secret="x" * 32, api_otp_pepper="x" * 32)


@pytest.mark.parametrize("api_env", ["stand", "prod"])
def test_sms_codes_in_the_log_are_refused_on_a_public_server(
    make_settings: SettingsFactory, api_env: str
) -> None:
    with pytest.raises(ValidationError, match="SMS_BACKEND=log"):
        make_settings(api_env=api_env, sms_backend="log")


FIXED_PHONES = ["+375291111111", "+375442222222"]


def test_fixed_sms_phones_are_parsed_from_a_comma_separated_env_var(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    monkeypatch.setenv("SMS_FIXED_PHONES", "+375291111111, +375442222222 ,")

    settings = make_settings(sms_backend="fixed", sms_fixed_code="482913")

    assert settings.sms_fixed_phones == FIXED_PHONES


def test_fixed_sms_is_allowed_in_prod(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        api_env="prod", sms_backend="fixed", sms_fixed_code="482913", sms_fixed_phones=FIXED_PHONES
    )

    assert settings.is_prod
    assert settings.sms_backend == "fixed"


@pytest.mark.parametrize("code", [None, "12345", "1234567", "12345a", "１２３４５６"])  # noqa: RUF001
def test_fixed_sms_needs_a_six_digit_code(make_settings: SettingsFactory, code: str | None) -> None:
    with pytest.raises(ValidationError, match="SMS_FIXED_CODE"):
        make_settings(sms_backend="fixed", sms_fixed_code=code, sms_fixed_phones=FIXED_PHONES)


@pytest.mark.parametrize(
    "code", ["000000", "111111", "999999", "012345", "123456", "456789", "987654", "543210"]
)
def test_fixed_sms_refuses_guessable_codes(make_settings: SettingsFactory, code: str) -> None:
    with pytest.raises(ValidationError, match="too easy to guess"):
        make_settings(sms_backend="fixed", sms_fixed_code=code, sms_fixed_phones=FIXED_PHONES)


def test_fixed_sms_needs_at_least_one_phone(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="SMS_FIXED_PHONES"):
        make_settings(sms_backend="fixed", sms_fixed_code="482913")


@pytest.mark.parametrize(
    "phone",
    ["375291111111", "+375 29 111 11 11", "+375171111111", "+79161111111", "+375２９1111111"],  # noqa: RUF001
)
def test_fixed_sms_phones_must_be_accepted_by_the_api(
    make_settings: SettingsFactory, phone: str
) -> None:
    with pytest.raises(ValidationError, match="SMS_FIXED_PHONES"):
        make_settings(
            sms_backend="fixed", sms_fixed_code="482913", sms_fixed_phones=[*FIXED_PHONES, phone]
        )


def test_fixed_sms_settings_are_ignored_by_other_backends(make_settings: SettingsFactory) -> None:
    assert make_settings(sms_backend="log", sms_fixed_phones=["garbage"]).sms_backend == "log"


def test_validation_errors_do_not_show_the_input(make_settings: SettingsFactory) -> None:
    # The last field in the input is the one a truncated repr would end with.
    with pytest.raises(ValidationError) as error:
        make_settings(sms_backend="fixed", sms_fixed_phones=FIXED_PHONES, sms_fixed_code="99999")

    assert "99999" not in str(error.value)
    assert "375442222222" not in str(error.value)


def test_sign_in_secrets_are_not_shown_in_repr(make_settings: SettingsFactory) -> None:
    settings = make_settings(api_jwt_secret="jwt-" + "a" * 32, api_otp_pepper="pep-" + "b" * 32)

    assert "a" * 32 not in repr(settings)
    assert "b" * 32 not in repr(settings)


def test_totp_key_is_decoded_from_base64(make_settings: SettingsFactory) -> None:
    key = bytes(range(32))

    settings = make_settings(api_totp_encryption_key=base64.b64encode(key).decode())

    assert settings.totp_encryption_key == key
    assert base64.b64encode(key).decode() not in repr(settings)


@pytest.mark.parametrize(
    "value",
    [
        base64.b64encode(b"k" * 31).decode(),
        base64.b64encode(b"k" * 33).decode(),
        "k" * 32,  # long enough as text, but not base64 of 32 bytes
        "not base64 at all!",
    ],
)
def test_totp_key_must_be_32_bytes_of_base64(make_settings: SettingsFactory, value: str) -> None:
    with pytest.raises(ValidationError, match="API_TOTP_ENCRYPTION_KEY") as error:
        make_settings(api_totp_encryption_key=value)

    assert value not in str(error.value)


def test_storage_is_off_without_keys_and_links_use_the_endpoint(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(s3_endpoint_url="http://seaweedfs:8333/")

    assert not settings.storage_configured
    assert settings.storage_public_url == "http://seaweedfs:8333"


def test_storage_keys_come_together(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="set together"):
        make_settings(s3_access_key="key")


@pytest.mark.parametrize(
    "url", ["seaweedfs:8333", "ftp://seaweedfs", "http://seaweedfs:8333/bucket", "https://"]
)
def test_storage_urls_are_scheme_and_host_only(make_settings: SettingsFactory, url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(s3_public_url=url)


def test_document_links_must_be_https_in_prod(make_settings: SettingsFactory) -> None:
    keys = {"s3_access_key": "key", "s3_secret_key": "secret"}

    with pytest.raises(ValidationError, match="must be https"):
        make_settings(api_env="prod", s3_public_url="http://files.example.com", **keys)
    settings = make_settings(api_env="prod", s3_public_url="https://files.example.com", **keys)
    assert settings.storage_public_url == "https://files.example.com"
    # Without keys there are no links, so a prod server without storage still starts.
    assert not make_settings(api_env="prod").storage_configured


def test_fleet_thresholds_default_to_the_documented_values(make_settings: SettingsFactory) -> None:
    settings = make_settings()

    assert settings.fleet_min_battery_pct == 20
    assert settings.fleet_telemetry_max_age_seconds == 300


@pytest.mark.parametrize(
    "overrides",
    [
        {"fleet_min_battery_pct": -1},
        {"fleet_min_battery_pct": 101},
        # Zero would make every car stale the moment its snapshot arrives.
        {"fleet_telemetry_max_age_seconds": 0},
        {"emulator_car_count": 0},
        {"emulator_car_count": 1001},
    ],
)
def test_fleet_settings_out_of_range_are_refused(
    make_settings: SettingsFactory, overrides: dict[str, int]
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


_API_SECRETS = (
    "API_JWT_SECRET",
    "API_OTP_PEPPER",
    "API_TOTP_ENCRYPTION_KEY",
    "REDIS_PASSWORD",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
)


def _worker_settings(**overrides: Any) -> WorkerSettings:
    values: dict[str, Any] = {
        "postgres_user": "test",
        "postgres_password": "test",
        "postgres_db": "test",
        **overrides,
    }
    return WorkerSettings(_env_file=None, **values)


def test_worker_starts_without_the_api_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    # The worker container gets only the database settings (docs/adr/0013).
    for name in _API_SECRETS:
        monkeypatch.delenv(name, raising=False)

    settings = _worker_settings()

    assert settings.auth_cleanup_retention_days == 7
    assert not any(hasattr(settings, name.lower()) for name in _API_SECRETS)


def test_empty_cleanup_retention_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Compose passes AUTH_CLEANUP_RETENTION_DAYS through even when .env leaves it empty.
    monkeypatch.setenv("AUTH_CLEANUP_RETENTION_DAYS", "")

    assert _worker_settings().auth_cleanup_retention_days == 7


@pytest.mark.parametrize("days", [0, -1])
def test_cleanup_retention_must_be_at_least_a_day(days: int) -> None:
    # Zero would delete a session the moment it is revoked, together with its reuse evidence.
    with pytest.raises(ValidationError):
        _worker_settings(auth_cleanup_retention_days=days)


def test_telemetry_secret_must_be_long(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="EMULATOR_TELEMETRY_SECRET"):
        make_settings(emulator_telemetry_secret="t" * 31)


def test_empty_telemetry_secret_leaves_telemetry_off(
    monkeypatch: pytest.MonkeyPatch, make_settings: SettingsFactory
) -> None:
    # docker compose passes an unset variable as an empty one.
    monkeypatch.setenv("EMULATOR_TELEMETRY_SECRET", "")

    assert make_settings().emulator_telemetry_secret is None


_PAYMENTS = {
    "payments_provider": "fake",
    "payments_webhook_secret": "w" * 32,
    "payments_token_encryption_key": base64.b64encode(b"p" * 32).decode(),
}


def test_payments_are_off_by_default(make_settings: SettingsFactory) -> None:
    settings = make_settings()

    assert settings.payments_provider is None
    assert settings.payments_token_key is None


def test_payments_token_key_is_decoded_from_base64(make_settings: SettingsFactory) -> None:
    settings = make_settings(**_PAYMENTS)

    assert settings.payments_token_key == b"p" * 32
    assert _PAYMENTS["payments_token_encryption_key"] not in repr(settings)


@pytest.mark.parametrize("missing", ["payments_webhook_secret", "payments_token_encryption_key"])
def test_payments_provider_needs_its_secrets(make_settings: SettingsFactory, missing: str) -> None:
    values = {name: value for name, value in _PAYMENTS.items() if name != missing}

    with pytest.raises(ValidationError, match="PAYMENTS_PROVIDER needs"):
        make_settings(**values)


def test_fake_payments_are_refused_in_production(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="PAYMENTS_PROVIDER=fake"):
        make_settings(**_PAYMENTS, api_env="prod")


def test_the_demo_stand_is_public_but_runs_on_test_payments(make_settings: SettingsFactory) -> None:
    # The stand (#123) is shown with test cards and no money, on the open internet.
    settings = make_settings(**_PAYMENTS, api_env="stand")

    assert settings.payments_provider == "fake"
    assert settings.is_public
    assert not settings.is_prod


def test_payments_webhook_secret_must_be_long(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="PAYMENTS_WEBHOOK_SECRET"):
        make_settings(**{**_PAYMENTS, "payments_webhook_secret": "w" * 31})


@pytest.mark.parametrize("value", [base64.b64encode(b"p" * 31).decode(), "not base64!", "p" * 32])
def test_payments_token_key_must_be_32_bytes_of_base64(
    make_settings: SettingsFactory, value: str
) -> None:
    with pytest.raises(ValidationError, match="PAYMENTS_TOKEN_ENCRYPTION_KEY") as error:
        make_settings(**{**_PAYMENTS, "payments_token_encryption_key": value})

    assert value not in str(error.value)


def test_payments_token_key_differs_from_the_totp_key(make_settings: SettingsFactory) -> None:
    totp_key = base64.b64encode(b"t" * 32).decode()

    with pytest.raises(ValidationError, match="must be different"):
        make_settings(
            **{**_PAYMENTS, "payments_token_encryption_key": totp_key},
            api_totp_encryption_key=totp_key,
        )


def test_command_secret_must_be_long(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="EMULATOR_COMMAND_SECRET"):
        make_settings(emulator_command_secret="c" * 31)


def test_command_and_telemetry_secrets_must_differ(make_settings: SettingsFactory) -> None:
    # A leaked webhook secret must not move cars.
    with pytest.raises(ValidationError, match="must differ"):
        make_settings(emulator_telemetry_secret="s" * 40, emulator_command_secret="s" * 40)


@pytest.mark.parametrize(
    "url", ["car-emulator:8100", "ftp://car-emulator", "http://car-emulator:8100?x=1"]
)
def test_emulator_url_must_be_plain_http(make_settings: SettingsFactory, url: str) -> None:
    with pytest.raises(ValidationError, match="EMULATOR_URL"):
        make_settings(emulator_url=url)


def test_emulator_url_loses_its_trailing_slash(make_settings: SettingsFactory) -> None:
    settings = make_settings(emulator_url="http://car-emulator:8100/")

    assert settings.emulator_url == "http://car-emulator:8100"
