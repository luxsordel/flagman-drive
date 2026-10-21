# 06. Онбординг

Цель: новый участник поднимает проект и делает первый PR в первый день.

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
gh auth login --web --scopes "project,read:org"
```

`gh auth login` откроет браузер — войдите в GitHub там. Пароли и токены в чат с Claude не вставляйте.

### Claude Code

1. Склонируйте репозиторий (`gh repo clone <owner>/flagman-drive`) в удобную папку, например `Documents\flagman-drive`.
2. В приложении Claude → вкладка **Code** → выберите эту папку.
3. Claude Code сам подхватит общие правила: `CLAUDE.md`, `.claude/settings.json` и команды из `.claude/skills/`.
4. Личные настройки (если нужны) — в `.claude/settings.local.json`, он не коммитится.
5. Проверьте: напишите в новой сессии «какие правила проекта ты знаешь?» — Claude должен пересказать `CLAUDE.md`.

## 2. Получить доступы

- Репозиторий GitHub (приглашение в организацию)
- Аккаунт Expo (для облачных сборок)
- Файл `.env` — **не** через мессенджер; через менеджер паролей. Шаблон — `.env.example`.

## 3. Запустить

Команды появятся после этапа 0 и будут описаны здесь. Ориентировочно:

```bash
git clone <repo-url>
cd flagman-drive
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d
```

## 4. Прочитать

1. [README](../README.md) → [цели](01-vision-and-mvp.md) → [архитектура](02-architecture.md)
2. [Безопасность](03-security.md) — обязательно
3. [Рабочий процесс](04-workflow.md) — как мы работаем и как пользоваться Claude Code
4. [CLAUDE.md](../CLAUDE.md)

## 5. Первая задача

Возьми задачу с меткой `good first issue`, пройди полный цикл: ветка → Claude Code → PR → ревью → merge.
