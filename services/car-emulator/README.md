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

## Машины

`EMULATOR_CAR_COUNT` машин (по умолчанию 30) появляются при старте в случайных точках Минска.
Случайность фиксирована `EMULATOR_SEED` (по умолчанию 42), поэтому у всех разработчиков парк одинаковый.
id машины — `uuid5(CAR_ID_NAMESPACE, "<seed>:<номер>")`, где номер считается от нуля. Это контракт с
сид-командой API (`flagman-fleet seed` в `apps/api`): она выводит те же id из тех же двух переменных,
и телеметрия сразу попадает на машины, которые знает API. Пространство имён и формат меняются только
вместе с API: эталонный вектор проверяют тесты обеих сторон.

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
на `EMULATOR_TELEMETRY_URL`, подписанный `EMULATOR_TELEMETRY_SECRET`. Его принимает API:
`POST /fleet/telemetry` (`apps/api`, модуль `fleet`). В Docker Compose адрес по умолчанию —
`http://api:8000/fleet/telemetry`. Если адрес пуст (эмулятор запущен отдельно), снимок уходит в заглушку,
которая пишет в лог только число машин. Если API недоступен или отказал, эмулятор пишет предупреждение
и шлёт следующий снимок по расписанию.

`updated_at` в телеметрии — время снимка, то есть момент отправки, а не последнего изменения машины.
По нему API считает свежесть: стоящая машина без изменений должна оставаться на карте. В ответах
`/cars` и на команды `updated_at` — время последнего изменения.

## Проверки

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy src tests && uv run pytest
```
