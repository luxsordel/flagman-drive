import pytest
from pydantic import ValidationError

from conftest import SettingsFactory


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


def test_sms_codes_in_the_log_are_refused_in_prod(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError, match="SMS_BACKEND=log"):
        make_settings(api_env="prod", sms_backend="log")


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
