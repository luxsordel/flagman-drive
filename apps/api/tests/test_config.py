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
