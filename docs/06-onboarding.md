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
| Expo Go / dev build | Запуск приложения на телефоне | Из App Store / Google Play |

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
   `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `API_JWT_SECRET`, `API_OTP_PEPPER`, `API_TOTP_ENCRYPTION_KEY`.
   Для локальной разработки придумайте их сами, например `openssl rand -hex 24` в Git Bash.
   `API_JWT_SECRET` и `API_OTP_PEPPER` — не короче 32 символов и разные (`openssl rand -hex 32` даёт 64),
   иначе API не стартует. `API_TOTP_ENCRYPTION_KEY` — ровно 32 байта в base64: `openssl rand -base64 32`.
   Если `.env` у вас уже был, перенесите в него из `.env.example` новые строки
   `API_JWT_SECRET`, `API_OTP_PEPPER`, `SMS_BACKEND`, `SMS_FIXED_CODE`, `SMS_FIXED_PHONES`,
   `API_TOTP_ENCRYPTION_KEY`, `S3_ENDPOINT_URL`, `S3_REGION` и `S3_PUBLIC_URL`; последние три локально можно
   оставить пустыми.
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

QR-код из терминала открыть в Expo Go на телефоне (телефон и компьютер — в одной сети); клавиша `w`
открывает веб-версию. Подробности и проверки — [apps/mobile/README.md](../apps/mobile/README.md).

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
