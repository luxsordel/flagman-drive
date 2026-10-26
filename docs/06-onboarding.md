# 06. Онбординг

Цель: новый участник поднимает проект и делает первый PR в первый день.

> **Самый быстрый путь:** вставьте [готовый промпт](onboarding-prompt.md) в новую сессию Claude Code.
> Он пройдёт все шаги ниже вместе с вами. Этот документ — ручная версия тех же шагов.

## 1. Установить (Windows 11)

| Что | Зачем | Как |
|---|---|---|
| Git | Код | `winget install Git.Git` |
| GitHub CLI | PR и задачи из терминала (им пользуется и Claude Code) | `winget install GitHub.cli` |
| Docker Desktop (WSL 2) | БД, Redis, хранилище | `winget install Docker.DockerDesktop`, затем перезагрузка |
| uv | Python и зависимости бэкенда | `winget install astral-sh.uv` |
| Node.js LTS (24) | Мобилка, админка | `winget install OpenJS.NodeJS.LTS` |
| Claude Code | AI-ассистент | Десктоп-приложение Claude, вкладка Code |
| Expo Go | Быстрый запуск приложения на телефоне (без карты) | Из App Store / Google Play |
| Android Studio | Dev-сборка с картой: эмулятор или телефон по USB (ADR-0016) | `winget install Google.AndroidStudio`, при первом запуске — мастер установки SDK |

Одной командой (PowerShell):

```powershell
winget install -e --id GitHub.cli; winget install -e --id astral-sh.uv; winget install -e --id OpenJS.NodeJS.LTS; winget install -e --id Docker.DockerDesktop
```

После установки **перезапустите терминал** (и приложение Claude), чтобы обновился `PATH`.
Python отдельно ставить не нужно — `uv` сам скачает нужную версию (3.12).

### Git и GitHub

```powershell
git config --global user.name "Ваше Имя"
git config --global user.email "почта@из-github"
git config --global core.autocrlf false   # окончания строк управляются .gitattributes
gh auth login --web --scopes "project,read:org,workflow"
```

`gh auth login` откроет браузер — войдите в GitHub там. Пароли и токены в чат с Claude не вставляйте.

### Claude Code

1. Склонируйте репозиторий (`gh repo clone luxsordel/flagman-drive`) в удобную папку, например `Documents\flagman-drive`.
2. В приложении Claude → вкладка **Code** → выберите эту папку.
3. Claude Code сам подхватит общие правила: `CLAUDE.md`, `.claude/settings.json` и команды из `.claude/skills/`.
4. Личные настройки (если нужны) — в `.claude/settings.local.json`, он не коммитится.
5. Проверьте: напишите в новой сессии «какие правила проекта ты знаешь?» — Claude должен пересказать `CLAUDE.md`.

## 2. Получить доступы

- Репозиторий GitHub — приглашение соавтором приходит на почту / в уведомления GitHub, его нужно принять
- Аккаунт Expo (для облачных сборок)
- Файл `.env` — **не** через мессенджер; через менеджер паролей. Шаблон — `.env.example`.

## 3. Запустить

### Инфраструктура: PostgreSQL + PostGIS, Redis, SeaweedFS (S3)

Docker Desktop должен быть запущен. Все команды — из корня репозитория, одинаково в PowerShell и Git Bash
(кроме копирования `.env`).

1. Создать `.env` из шаблона:

   ```powershell
   Copy-Item .env.example .env   # PowerShell
   ```

   ```bash
   cp .env.example .env          # Git Bash
   ```

2. Заполнить в `.env` пустые значения: `API_ENV` (локально `dev`), `POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
   `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `API_JWT_SECRET`, `API_OTP_PEPPER`, `API_TOTP_ENCRYPTION_KEY`,
   `EMULATOR_COMMAND_SECRET`, `EMULATOR_TELEMETRY_SECRET`, `PAYMENTS_WEBHOOK_SECRET`,
   `PAYMENTS_TOKEN_ENCRYPTION_KEY`.
   Для локальной разработки придумайте их сами, например `openssl rand -hex 24` в Git Bash.
   Секреты эмулятора — не короче 32 символов и разные. Оба читают и эмулятор, и API:
   `EMULATOR_TELEMETRY_SECRET` подписывает телеметрию, `EMULATOR_COMMAND_SECRET` — ручные команды машинам
   из раздела «Парк» админки.
   `API_JWT_SECRET` и `API_OTP_PEPPER` — не короче 32 символов и разные (`openssl rand -hex 32` даёт 64),
   иначе API не стартует. `API_TOTP_ENCRYPTION_KEY` — ровно 32 байта в base64: `openssl rand -base64 32`.
   `PAYMENTS_WEBHOOK_SECRET` — не короче 32 символов, `PAYMENTS_TOKEN_ENCRYPTION_KEY` — тоже 32 байта в
   base64 и не равен `API_TOTP_ENCRYPTION_KEY`. Они нужны тестовому платёжному провайдеру
   (`PAYMENTS_PROVIDER=fake`): без них API с этим провайдером не стартует. Если оставить `PAYMENTS_PROVIDER`
   пустым, API работает, но карты привязать нельзя. С `API_ENV=prod` тестовый провайдер запрещён: на сервере
   пилота `PAYMENTS_PROVIDER` оставьте пустым, пока не подключён настоящий.
   Если `.env` у вас уже был, перенесите в него из `.env.example` новые строки
   `API_JWT_SECRET`, `API_OTP_PEPPER`, `SMS_BACKEND`, `SMS_FIXED_CODE`, `SMS_FIXED_PHONES`,
   `API_TOTP_ENCRYPTION_KEY`, `S3_ENDPOINT_URL`, `S3_REGION`, `S3_PUBLIC_URL`, `FLEET_MIN_BATTERY_PCT`,
   `FLEET_TELEMETRY_MAX_AGE_SECONDS`, `TELEMATICS_SIGNATURE_TOLERANCE_SECONDS`, `EMULATOR_SEED`,
   `EMULATOR_URL`, `TELEMATICS_COMMAND_TIMEOUT_SECONDS`, `PAYMENTS_PROVIDER`, `PAYMENTS_WEBHOOK_SECRET`,
   `PAYMENTS_TOKEN_ENCRYPTION_KEY` и `PAYMENTS_WEBHOOK_TOLERANCE_SECONDS`.
   Локально можно оставить пустыми `S3_ENDPOINT_URL`, `S3_REGION`, `S3_PUBLIC_URL`, `FLEET_MIN_BATTERY_PCT`,
   `FLEET_TELEMETRY_MAX_AGE_SECONDS`, `TELEMATICS_SIGNATURE_TOLERANCE_SECONDS`, `EMULATOR_URL`
   (в Compose это сервис `car-emulator`) и `TELEMATICS_COMMAND_TIMEOUT_SECONDS`; `EMULATOR_SEED`
   возьмите как в шаблоне. Для раздела «Парк» админки впишите её адрес в `API_CORS_ORIGINS`
   (`http://127.0.0.1:5173`): живой поток парка принимает только эти адреса
   ([ADR-0017](adr/0017-fleet-stream-websocket.md)). Если в старом `.env` у `EMULATOR_TELEMETRY_URL` стоит адрес, очистите его:
   пустое значение в Compose — это API из того же Compose.
   На сервере пилота `API_ENV=prod`, а `S3_PUBLIC_URL` — `https`-адрес хранилища, по которому клиенты
   открывают фото документов ([ADR-0012](adr/0012-kyc-document-storage.md)).
   Пустое значение — ошибка запуска, это сделано специально (SeaweedFS без ключей открыт анонимно).

3. Поднять:

   ```bash
   docker compose --env-file .env -f infra/docker-compose.yml up -d
   ```

   `--env-file .env` обязателен: без него Compose ищет `.env` в папке `infra/`.

4. Проверить — через 10–20 секунд:

   ```bash
   docker compose --env-file .env -f infra/docker-compose.yml ps -a
   ```

   `postgres`, `redis`, `seaweedfs` — `Up (healthy)`; `seaweedfs-init` — `Exited (0)`
   (это одноразовая задача, которая создаёт приватный бакет `S3_BUCKET`).
   `worker` — `Up` без пометки `healthy`: проверки здоровья у него нет, это фоновые задачи
   ([ADR-0013](adr/0013-periodic-jobs-worker.md)).

5. Применить миграции и добавить демо-машины — команды в [`apps/api/README.md`](../apps/api/README.md)
   (разделы «Запуск в Docker» и «Демо-парк»).

6. Проверить, что телеметрия доходит до API. Эмулятор шлёт снимок каждые 5 секунд:

   ```bash
   docker compose --env-file .env -f infra/docker-compose.yml logs --tail 20 api
   ```

   Ищите строки `"path": "/fleet/telemetry", "status": 200`. Ответ `401` значит, что у API и эмулятора
   разные `EMULATOR_TELEMETRY_SECRET` или расходятся часы. Ответ `503` значит, что API не получил секрет.
   Если API запущен на хосте через `uv run`, а эмулятор в Docker, задайте
   `EMULATOR_TELEMETRY_URL=http://host.docker.internal:8000/fleet/telemetry`.

| Сервис | Адрес | Доступ |
|---|---|---|
| PostgreSQL 16 + PostGIS | `127.0.0.1:5432` | `POSTGRES_USER` / `POSTGRES_PASSWORD`, база `POSTGRES_DB` |
| Redis 7 | `127.0.0.1:6379` | пароль `REDIS_PASSWORD` |
| SeaweedFS (S3 API) | `http://127.0.0.1:8333` | ключи `S3_ACCESS_KEY` / `S3_SECRET_KEY` |

Порты открыты только на `127.0.0.1` — из локальной сети сервисы не видны. Если порт занят, поменяйте
`*_PORT` в `.env`. Почему SeaweedFS, а не MinIO — [ADR-0005](adr/0005-object-storage-seaweedfs.md).

Остановить (данные сохраняются в Docker volumes):

```bash
docker compose --env-file .env -f infra/docker-compose.yml down
```

Сбросить всё с нуля (**удаляет** базу, Redis и файлы хранилища):

```bash
docker compose --env-file .env -f infra/docker-compose.yml down -v
```

Если не стартует: `docker compose --env-file .env -f infra/docker-compose.yml logs <сервис>`.

### Клиентское приложение (`apps/mobile`)

Из корня репозитория:

```bash
npm install
```

```bash
npm run start --workspace @flagman/mobile
```

Клавиша `w` открывает веб-версию — в ней работает и карта. На телефоне есть два способа
([ADR-0016](adr/0016-maplibre-react-native-dev-build.md)):

- **Expo Go — быстро, но без карты.** `npm run go --workspace @flagman/mobile`, QR-код открыть в Expo Go
  (телефон и компьютер — в одной сети). Вход, документы и профиль работают, вместо карты — заглушка.
- **Dev-сборка через Android Studio — с картой.** Нужны Android Studio с Android SDK, переменная `ANDROID_HOME`
  и `JAVA_HOME` на JDK из Android Studio. Запустите эмулятор (Device Manager) или подключите телефон по USB
  с отладкой, затем `npm run android --workspace @flagman/mobile`. Первая сборка занимает 10–15 минут.

Подробности, адрес API и проверки — [apps/mobile/README.md](../apps/mobile/README.md).

### Админ-панель (`apps/admin`)

Из корня репозитория — установить JS-зависимости всех workspaces и запустить dev-сервер:

```bash
npm install
```

```bash
npm run dev --workspace @flagman/admin
```

Панель открывается на http://127.0.0.1:5173. Подробнее — [apps/admin/README.md](../apps/admin/README.md).

## 4. Прочитать

1. [README](../README.md) → [цели](01-vision-and-mvp.md) → [архитектура](02-architecture.md)
2. [Безопасность](03-security.md) — обязательно
3. [Рабочий процесс](04-workflow.md) — как мы работаем и как пользоваться Claude Code
4. [CLAUDE.md](../CLAUDE.md)

## 5. Первая задача

Возьми задачу с меткой `good first issue`, пройди полный цикл: ветка → Claude Code → PR → ревью → merge.
