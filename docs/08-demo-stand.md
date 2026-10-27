# Демо-стенд

Публичный стенд пилота: админка по ссылке, веб-версия приложения, APK для Android. Работает на тестовых данных,
без реальных денег и SMS. Почему он устроен именно так — [ADR-0021](adr/0021-demo-stand-digitalocean-ghcr.md).

## Как устроен

```
              Интернет: только 80/443 (и 22 для выкладки)
                              │
                      Caddy (образ web)
   admin.<домен>   app.<домен>     api.<домен>     s3.<домен>
   админка +       веб-версия      API для         подписанные ссылки
   /staff/,/admin/ приложения      приложения      на фото
        │                              │                 │
        └──────────── api:8000 ────────┘           seaweedfs:8333
                         │
      postgres · redis · worker · car-emulator · backup   ← только внутренняя сеть
```

- Один дроплет DigitalOcean во Франкфурте, всё в Docker Compose: `infra/docker-compose.yml` + слой стенда
  `infra/stand/compose.yml`.
- Образы `flagman-api`, `flagman-car-emulator`, `flagman-web` лежат в GHCR, тег — первые 12 символов SHA коммита.
- API работает с `API_ENV=stand`: защиты как в проде (HSTS, скрытая документация API, коды не пишутся в лог,
  ссылки на фото только по https), но тестовые платежи и демо-данные разрешены.
- Домен пока не выбран, ниже он обозначен `<домен>`; в настройках это `STAND_DOMAIN`.

## Где лежат секреты

Значений нет нигде в репозитории. Шаблон настроек сервера — `infra/stand/stand.env.example`.

| Что | Где |
| --- | --- |
| Ключи API (JWT, OTP, TOTP), пароли Postgres и Redis, ключи S3, секреты эмулятора и платежей | `/opt/flagman/stand.env` на сервере (`chmod 600`, владелец `deploy`) |
| Код OTP стенда и тестовые номера | там же: `SMS_FIXED_CODE`, `SMS_FIXED_PHONES` |
| Пароли и 2FA-секреты тестовых сотрудников | там же: `STAND_<РОЛЬ>_*`; копия — у тех, кто показывает стенд, в менеджере паролей |
| SSH-ключ выкладки, адрес сервера, ключ хоста | GitHub → Settings → Environments → `stand`: `STAND_SSH_KEY`, `STAND_SSH_HOST`, `STAND_SSH_KNOWN_HOSTS` |
| Домен стенда (не секрет) | GitHub → Settings → Variables: `STAND_DOMAIN` |
| Адрес API для APK (не секрет) | EAS → проект → Environment variables → `preview`: `EXPO_PUBLIC_API_URL` |

## Первая настройка (один раз)

1. **Дроплет.** DigitalOcean → Create Droplet: Frankfurt (`fra1`), Ubuntu 24.04 LTS, Basic 2 vCPU / 4 GB,
   вход только по SSH-ключу. Включить еженедельные бэкапы дроплета (Backups → Weekly): копии базы, которые
   делает сам стенд, лежат на том же дроплете и пропадут вместе с ним.
2. **Firewall.** Networking → Firewalls: входящие только TCP 22, TCP 80, TCP 443 и UDP 443; привязать к дроплету.
3. **Сервер** (под `root`, один раз):
   ```bash
   apt-get update && apt-get -y upgrade && apt-get -y install unattended-upgrades
   curl -fsSL https://get.docker.com | sh
   adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy
   install -d -o deploy -g deploy -m 750 /opt/flagman
   install -d -o deploy -g deploy -m 700 /home/deploy/.ssh
   # ключ выкладки: публичная часть — в authorized_keys, приватная — только в Environment stand
   ssh-keygen -t ed25519 -N "" -C flagman-stand-deploy -f /root/stand-deploy
   install -o deploy -g deploy -m 600 /root/stand-deploy.pub /home/deploy/.ssh/authorized_keys
   # вход по паролю и под root закрыт
   sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
   systemctl reload ssh
   ```
   Содержимое `/root/stand-deploy` скопировать в секрет `STAND_SSH_KEY` и удалить файл с сервера.
   До `systemctl reload ssh` заведите себе личного пользователя с `sudo` и своим ключом и проверьте вход под ним:
   после перезагрузки SSH под `root` уже не войти, а `deploy` — только для выкладки.
4. **Настройки стенда.** Скопировать `infra/stand/stand.env.example` в `/opt/flagman/stand.env`, заполнить
   (каждый секрет сгенерировать на сервере — команды в комментариях шаблона), `chmod 600`, владелец `deploy`.
5. **DNS.** A-записи `admin.`, `app.`, `api.`, `s3.` домена → IP дроплета. Без них Caddy не получит сертификаты.
6. **GitHub.** Settings → Environments → `stand`: в Deployment protection rules оставить только тех, кто может
   выкладывать; в Deployment branches and tags выбрать «Selected branches and tags» и разрешить только ветку
   `main` и теги `stand-v*` — иначе секреты достанутся запуску с любой ветки с изменённым `stand.yml`. Добавить секреты `STAND_SSH_KEY`, `STAND_SSH_HOST` (IP дроплета) и
   `STAND_SSH_KNOWN_HOSTS` (вывод `ssh-keyscan -t ed25519 <IP>`, сверенный с отпечатком в консоли DigitalOcean).
   В Settings → Variables добавить `STAND_DOMAIN`.
7. **Первая выкладка** — как обычная, ниже. После неё в GitHub → Packages у трёх образов проверить, что
   видимость — private.

## Выкладка

Actions → **stand** → Run workflow, поле `tag` пустое. Или тег `stand-v*` на нужный коммит
(`git tag stand-v0.4.0 && git push origin stand-v0.4.0`). На каждый коммит в `main` стенд не обновляется.

Workflow собирает образы, копирует на сервер `infra/` из коммита этих образов и запускает
`infra/stand/bin/deploy.sh <тег>`:
1. скачивает образы;
2. проверяет пароли и 2FA-секреты сотрудников из `stand.env` (`flagman-staff ensure --check`): с ошибкой в
   `stand.env` выкладка останавливается, пока ничего не изменено;
3. если схема базы новее нового образа (откат) — опускает её прежним образом;
4. применяет миграции (`alembic upgrade head`);
5. запускает сид: зона, парк, тарифы, сотрудники. Сид повторяемый: существующее не трогает;
6. перезапускает сервисы и ждёт `/ready`;
7. дописывает тег в `/opt/flagman/deployed-tags.log` и удаляет образы старше пяти последних выкладок.

Логику скриптов проверяют тесты `infra/stand/tests/` на подставных `docker` и `pg_dump`, в CI — вместе с shellcheck.

В конце workflow проверяет `https://api.<домен>/health` снаружи.

## Откат

1. На сервере: `tail -n 5 /opt/flagman/deployed-tags.log` — предпоследняя строка и есть предыдущая версия
   (или тег из прошлого запуска workflow в Actions).
2. Actions → **stand** → Run workflow, в поле `tag` — этот тег. Сборки не будет, выкладываются старые образы
   вместе с `infra/` их коммита. Образы пяти последних выкладок лежат на сервере, более старые скачиваются из GHCR.

Миграции откатываются сами (шаг 3 выкладки), если у них работает `downgrade`. Если `downgrade` теряет данные или
не написан — откат через восстановление из копии, ниже.

## Резервные копии

- Сервис `backup` раз в сутки делает `pg_dump` в том `pgbackups` и удаляет копии старше `BACKUP_KEEP_DAYS` дней
  (по умолчанию 7). Фото в SeaweedFS не копируются: они тестовые.
- Копия вручную: `docker compose ... exec backup sh /stand/bin/backup.sh` (ниже — как вызвать compose).
- Копии лежат на том же дроплете: они спасают от ошибки в данных или миграции, но не от потери дроплета.
  От неё — еженедельные бэкапы дроплета DigitalOcean (шаг 1 настройки): потеряются данные не больше чем за неделю.

## Восстановление из копии

```bash
cd /opt/flagman
infra/stand/bin/restore.sh                                   # список копий
infra/stand/bin/restore.sh flagman-20261028T030000Z.dump     # восстановить
```

Скрипт сначала делает копию текущего состояния, останавливает API, воркер, эмулятор и Caddy, пересоздаёт базу,
загружает копию, применяет миграции текущего образа и поднимает стенд обратно.

## Вход на стенд

- **Клиент:** номер из `SMS_FIXED_PHONES` и код `SMS_FIXED_CODE`. С любого другого номера код не приходит —
  это проверяет тест `apps/api/src/flagman/identity/tests/test_pilot_sign_in.py`.
- **Сотрудники:** `https://admin.<домен>`, почта и пароль из `STAND_<РОЛЬ>_*`, код из приложения-аутентификатора,
  в которое добавлен `STAND_<РОЛЬ>_TOTP_SECRET`. QR-кода при первом входе нет: 2FA уже настроена.
- Изменить пароль или 2FA существующего сотрудника правкой `stand.env` нельзя — сид не трогает существующих.
  Для этого `flagman-staff set-password` / `reset-totp` (`apps/api/README.md`) через `run --rm api`.

## Мобильные сборки

Версия приложения — только `version` в `apps/mobile/package.json` (`npm version minor -w @flagman/mobile`).
Номер сборки ведёт EAS и увеличивает сам при каждой сборке профиля `stand`.

Один раз:
```bash
cd apps/mobile
npx eas-cli login
npx eas-cli init          # создаёт проект в Expo; projectId добавить в app.config.ts (extra.eas.projectId)
npx eas-cli env:create --environment preview --name EXPO_PUBLIC_API_URL --value https://api.<домен> --visibility plaintext
```

**Android:** `npx eas-cli build --profile stand --platform android`. В конце EAS даёт ссылку и QR-код на APK —
её и отправляем. Сборка без https-адреса API падает сразу, а не уезжает к людям с `localhost` внутри.

**iOS:** аккаунта Apple Developer нет, поэтому TestFlight недоступен. На iPhone открывают `https://app.<домен>`
в Safari → «Поделиться» → «На экран „Домой“». Профиль EAS для TestFlight добавим, когда появится аккаунт.

## Обслуживание

Все команды — на сервере под `deploy`:

```bash
cd /opt/flagman
stand() { STAND_TAG="$(tail -n 1 deployed-tags.log | cut -d ' ' -f 2)" docker compose \
  --env-file stand.env -f infra/docker-compose.yml -f infra/stand/compose.yml "$@"; }
stand ps                       # что запущено
stand logs --tail 100 api      # логи; ротация — 5 файлов по 10 МБ на контейнер
stand exec backup ls -lh /backups
```

Health-check снаружи: `https://api.<домен>/health` (жив) и `https://api.<домен>/ready` (база и Redis доступны).
