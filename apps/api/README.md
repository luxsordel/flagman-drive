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
docker compose --env-file .env -f infra/docker-compose.yml up -d --build api worker
```

У API и `worker` один образ, поэтому пересобирать и перезапускать их нужно вместе: иначе worker останется
на старом коде.

Миграции — отдельной одноразовой командой:

```bash
docker compose --env-file .env -f infra/docker-compose.yml run --rm api alembic upgrade head
```

## Периодические задачи

`flagman-worker` раз в час удаляет истёкшие коды входа и закончившиеся больше
`AUTH_CLEANUP_RETENTION_DAYS` дней назад сессии ([ADR-0013](../../docs/adr/0013-periodic-jobs-worker.md)).
Локально:

```bash
uv run flagman-worker
```

В Docker это сервис `worker`, он поднимается вместе с остальными.

## Сотрудники

Сотрудников админки создаёт команда `flagman-staff` (управления в интерфейсе пока нет, см.
[ADR-0011](../../docs/adr/0011-staff-auth-password-totp.md)). Пароль она спрашивает дважды, в аргументах его нет.
2FA сотрудник подключает сам при первом входе в админку.

```bash
uv run flagman-staff create --email admin@flagman.by --role admin
```

В Docker — из корня репозитория:

```bash
docker compose --env-file .env -f infra/docker-compose.yml run --rm api flagman-staff create --email admin@flagman.by --role admin
```

Роли: `operator`, `technician`, `admin`. Остальные команды завершают все сессии сотрудника. Уже выданный
access-токен работает ещё до 15 минут.

Пароль забыт или утёк:

```bash
uv run flagman-staff set-password --email admin@flagman.by
```

Потерян телефон с аутентификатором или сменён `API_TOTP_ENCRYPTION_KEY`. Если есть подозрение на взлом, сменить
и пароль: до следующего входа подключить 2FA можно с одним паролем.

```bash
uv run flagman-staff reset-totp --email admin@flagman.by
```

Сотрудник уволился или аккаунт взломан:

```bash
uv run flagman-staff disable --email admin@flagman.by
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
