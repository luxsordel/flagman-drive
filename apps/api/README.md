# apps/api — бэкенд FLAGMAN Drive

FastAPI, модульный монолит. Архитектура — [docs/02-architecture.md](../../docs/02-architecture.md),
формат ошибок — [ADR-0006](../../docs/adr/0006-api-error-format.md).

## Структура

```
src/flagman/
  core/        # конфиг, БД, Redis, ошибки, логи, заголовки безопасности, фабрика приложения
  health/      # GET /health (liveness), GET /ready (БД + Redis)
  <module>/    # api, schemas, service, domain, repository, models, ports, tests
migrations/    # Alembic
tools/         # собственный контракт import-linter (границы модулей)
tests/         # общие тесты приложения; тесты модулей — в src/flagman/<module>/tests
```

## Запуск локально

Все команды — из `apps/api`, работают в PowerShell и Git Bash. Python 3.12 uv скачает сам.

```bash
uv sync
```

Поднять Postgres и Redis (из корня репозитория, `.env` заполнен по `.env.example`):

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d postgres redis
```

Применить миграции и запустить API с автоперезагрузкой. Настройки читаются из `../../.env`;
для запуска вне Docker `POSTGRES_HOST` и `REDIS_HOST` по умолчанию — `localhost`.

```bash
uv run alembic upgrade head
```

```bash
uv run uvicorn flagman.core.app:create_app --factory --reload
```

Swagger: http://127.0.0.1:8000/docs (выключен при `API_ENV=prod`).

## Запуск в Docker

Из корня репозитория:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build api
```

Миграции — отдельной одноразовой командой:

```bash
docker compose --env-file .env -f infra/docker-compose.yml run --rm api alembic upgrade head
```

## Проверки

```bash
uv run ruff format --check .
```

```bash
uv run ruff check .
```

```bash
uv run mypy
```

```bash
uv run lint-imports
```

```bash
uv run pytest
```

Интеграционные тесты (`-m integration`) поднимают Postgres и Redis через testcontainers —
нужен запущенный Docker Desktop. Без Docker их можно пропустить: `uv run pytest -m "not integration"`.

## Границы модулей

Модуль не импортирует `models` и `repository` другого модуля — только его `service`.
Правило проверяет `lint-imports` (контракт `tools/import_contracts.py`, настройка — в `pyproject.toml`).
