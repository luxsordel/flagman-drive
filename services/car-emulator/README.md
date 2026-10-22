# car-emulator

Эмулятор автопарка вместо реальной телематики. Держит N виртуальных машин в Минске в памяти, принимает
команды и шлёт телеметрию вебхуками. Обе стороны подписываются HMAC — формат в
[ADR-0007](../../docs/adr/0007-telematics-hmac-signing.md).

## Запуск

Через Docker Compose из корня репозитория (секреты `EMULATOR_*` — в `.env`, см. `.env.example`):

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d car-emulator
```

Локально, из `services/car-emulator`:

```bash
uv sync
uv run car-emulator
```

Перед локальным запуском задайте `EMULATOR_COMMAND_SECRET` и `EMULATOR_TELEMETRY_SECRET`
(по 32+ символа, разные).

## API

| Метод | Путь | Подпись |
|---|---|---|
| `GET` | `/health` | не нужна |
| `GET` | `/cars` | нужна |
| `GET` | `/cars/{car_id}` | нужна |
| `POST` | `/cars/{car_id}/commands` — `{"command_id": "<uuid>", "type": "open" \| "close" \| "lock_engine"}` | нужна |

Ответы на ошибки: `401 invalid_signature`, `404 car_not_found`, `409 duplicate_command`,
`409 command_rejected:<причина>` (например, `close` при включённом зажигании), `413 body_too_large`.

## Телеметрия

Каждые `EMULATOR_TELEMETRY_INTERVAL_S` секунд (по умолчанию 5) снимок всех машин уходит `POST`-запросом
на `EMULATOR_TELEMETRY_URL`, подписанный `EMULATOR_TELEMETRY_SECRET`. Пока адрес пуст, снимок уходит
в заглушку, которая пишет в лог только число машин.

## Проверки

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy src tests && uv run pytest
```
