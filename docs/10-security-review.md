# Проверка безопасности

Проход по правилам [docs/03](03-security.md) перед итоговым замером пилота (#162). Дата — 6 ноября 2026, ветка
`chore/162-optimization-security-review` (26 коммитов от `main`, последний — `301d7ee`).

Для каждого правила записано, чем оно подтверждено: тестом (`файл::тест`), настройкой или чтением кода
(`файл:строка`), ручным запуском. В путях `A/` означает `apps/api/src/flagman/`.

**Что проверялось.**

- Чек-лист `docs/03`, по пункту за раз. Тесты, на которые ссылается таблица, прогнаны локально на Postgres из
  testcontainers, кроме оговорённых в строках. Тесты коммитов после первого прохода (`80b0368`…`301d7ee`)
  перепрогнаны на `301d7ee`.
- Матрица доступа: новый тест обходит все маршруты API и проверяет роли, 401 и 403 до обращения к БД.
- Сканеры: gitleaks по всей истории, `pip-audit`, `npm run audit`, Semgrep — локально теми же версиями и
  командами, что в CI. Перепрогнаны на `301d7ee`, после всех коммитов первого прохода.
- Покрытие `domain.py`: та же команда, что в job `domain-coverage`, на `301d7ee`.
- Стендовый Caddyfile: локальный запуск Caddy, заголовки и строки лога. `scripts/check-caddy.mjs` перепрогнан
  на `301d7ee`.

**Чего не было.** Стенд ещё не выложен: у `stand.yml` нет ни одного запуска, домен не выбран
([docs/08](08-demo-stand.md)). Поэтому нет ZAP baseline, прогона `load:stand` и проверки логов этого прогона на
персональные данные и токены. Эти пункты помечены «ждёт стенда», порядок работ — в последнем разделе.
Оптимизационная половина #162 сделана только локально: узкие места найдены и исправлены по прогонам `load:local` на
компьютере разработчика, а замера на стенде нет (см. [docs/09](09-load-test.md), «Оптимизация, 6–7 ноября 2026»).
Ветка не прошла CI на GitHub и PR с `/security-review` и ревью человеком ещё не открыт. Состояние каждого
критерия #162 — в разделе «Критерии приёмки #162». Пока всё это не сделано, #162 остаётся открытой.

## Итог

- **Права доступа.** Добавлена матрица доступа по всем маршрутам (`7fddb2d`) и тесты на чужие объекты и подмену
  дочернего id (`c8d73f8`). Настоящих IDOR не найдено.
- **Живая сессия сотрудника.** Начало задачи техника (`1d93275`) и одобрение KYC (`c87b6a8`) раньше работали по
  токену ещё до 15 минут после отзыва сессии. Теперь проверяется сессия в БД.
- **Лимиты.** Ссылки на фото осмотров — 600 в час на сотрудника (`ac98aa3`). Принудительное завершение учитывается
  в лимите команд сотрудника (`b15712e`). Денежные действия сотрудника — 30 в минуту (`3d39dcd`, админка сообщает
  время ожидания — `883f10b`). Тесты на 429 там, где лимит был без теста (`7ae8678`, `0d2fa40`), и тест на
  корректировку без `Idempotency-Key` (`0d2fa40`). Лимиты сотрудника на действия с арендой и штрафами описаны
  в docs/02, комментарий в админке о 429 на корректировку и повтор возврата поправлен (`c15916a`).
- **Заблокированный клиент.** Машину клиента, заблокированного посреди поездки, нельзя было закрыть. Теперь ему
  разрешена только команда «закрыть» (`c2c0669`, ADR-0026).
- **Заголовки Caddy.** Собственные ответы Caddy об ошибках (502, 404) шли без HSTS, `nosniff`, `Referrer-Policy`
  и с `Server: Caddy`. Теперь заголовки на них есть, `Server` убран, у `s3.` появился CSP, `check-caddy.mjs`
  проверяет это на пяти ответах (`80b0368`).
- **Логи.** Caddy больше не пишет query-строки, IP и заголовки (`79c8a4e`). uvicorn больше не пишет адрес клиента
  при WebSocket-рукопожатии (`b394746`). Общий процессор логов заменяет на `[redacted]` значения полей из явного
  списка (`54e3c09`).
- **Аудит.** Загрузка фото задачи техником пишется в аудит (`366bbd4`).
- **Входные данные и секреты.** Мета-тест проверяет `extra="forbid"` у всех моделей тела и query (`0d2fa40`).
  Общий код SMS для `fixed` должен иметь не меньше четырёх разных цифр (`c83c021`). `.gitignore` закрывает все
  env-файлы, кроме примеров, Dependabot следит за образом стенда (`73af880`).
- **CI.** Из исключений `npm audit` удалены две устаревшие записи, устаревшая запись теперь роняет CI (`b42257b`),
  запись без id `GHSA-*`, причины или со сроком дальше 92 дней тоже (`a97e5ff`). Покрытие `domain.py` не ниже 80%
  проверяет отдельный job (`1de45e5`, `b0a3402`), локально на `301d7ee` — 98,4%. Предупреждения о всплесках неудачных входов и команд машинам (`61b6e3e`, `f25de50`,
  ADR-0029).
- **docs/03** сверен с кодом (`301d7ee`): окно 30 секунд для refresh, где лежат причины, охват `extra="forbid"`,
  общее маскирование логов, недостающие пункты «Перед боевым запуском».
- **Осталось в пилоте.** Пробелов в таблице два: команда машине может остаться без записи в аудите, если БД
  упадёт после отправки (нужно решение, #165); эта ветка ещё не прошла `/security-review` и ревью человеком в PR. Нужно решение и по клиенту,
  у которого KYC отозвали во время поездки: закрыть машину командой он не может (#166). Остаются: фильтр `log default`
  в Caddyfile больше не проверяется скриптом; маскирование логов ловит только поля с известными именами; образы
  docker-compose вне Dependabot; код SMS в `stand.env` на сервере проверить перед первой выкладкой. Ещё несколько
  мелких правок предложены ниже. Коммиты ветки ещё не проходили CI на GitHub.
- **Оптимизация по нагрузочному тесту сделана локально.** Диагностика прогона `pilot` показала, что API упирается в
  один цикл событий, а не в базу. Исправлено шестью коммитами: несколько процессов uvicorn (`1cf61c1`), один
  клиент хранилища на процесс (`9744659`), одна транзакция карты (`c819a0d`), настраиваемый пул БД (`7244eee`),
  keep-alive (`cc20bfd`), пакетная запись телеметрии (`b8a3175`). У каждой правки свой замер до и после. Серия
  `pilot` A, B, A, B с одним процессом: с правками все цели выполнены в обоих прогонах, обрывов карты нет, задержка
  телеметрии ниже и в паре с обратным порядком. По остальным p95 разница в пределах разброса между прогонами и
  тренда серии, выигрыша серия не показывает, эти правки подтверждены своими замерами. Два процесса в отдельной серии снизили p95 в 2–7 раз. Прогона на
  стенде нет, итоговый код с двумя процессами прогоном не мерился. См. docs/09, «Оптимизация, 6–7 ноября 2026».
- **Ждёт стенда и боевого запуска.** ZAP, `load:stand`, логи прогона. До боевого запуска — пентест, юридическая
  проверка ПД, сроки хранения, отдельные роли БД вместо суперпользователя, certificate pinning и другое из раздела
  «Отложено». Отдельные задачи: роли БД (#167), сканы по расписанию и Dependabot alerts (#168), ZAP с авторизацией
  (#169), лимит тела запроса в Caddy (#170).

## Критерии приёмки #162

| Критерий | Состояние | Где подробнее |
|---|---|---|
| Прогон `npm run load:stand` против стенда, результат в docs/09 | ждёт стенда | «Что ждёт стенда» |
| Узкие места исправлены, каждая оптимизация с замером до и после | сделано локально: шесть правок, у каждой микробенч, счётчики или серия прогонов; серия `pilot` A, B, A, B со всеми правками, кроме нескольких процессов: обрывов карты нет, задержка телеметрии ниже, остальные p95 в пределах разброса. Замер на стенде ждёт стенда | docs/09, «Оптимизация, 6–7 ноября 2026» и «Оптимизации (#162)» |
| docs/09 дополнен: найдено, исправлено, замеры до и после | сделано для локальных прогонов, включая отклонённые гипотезы и оговорку о разбросе; результат `load:stand` допишем после прогона на стенде | docs/09, «Оптимизация, 6–7 ноября 2026» |
| Чек-лист docs/03 пройден по пунктам, у каждого — где и как проверено | пройден, у каждого пункта записана проверка; два пробела: аудит команд при сбое БД и `/security-review` с ревью человеком для этого PR | «Таблица по правилам» |
| У всех эндпоинтов с объектами тесты на IDOR и права ролей, недостающие дописаны | сделано (`7fddb2d`, `c8d73f8`) | «Авторизация» |
| Rate limit на входе, OTP, загрузке файлов, привязке карты, чате | сделано, тесты на 429 у каждого лимита | «Транспорт и заголовки» |
| Заголовки безопасности и CORS в API и за Caddy | заголовки — сделано, CORS через Caddy проверить на стенде | «Транспорт и заголовки», «Что ждёт стенда» |
| Логи прогона нагрузочного теста без ПД и токенов | ждёт стенда | «Что ждёт стенда» |
| Сканеры зелёные, исключения в allowlist актуальны | локально зелёные на `301d7ee`, allowlist пуст; в CI ветки ещё не запускались | «Секреты», «Зависимости и CI» |
| OWASP ZAP baseline по API и админке | ждёт стенда | «Что ждёт стенда» |
| Покрытие доменной логики ≥ 80% проверяется в CI | job добавлен (`1de45e5`, `b0a3402`), локально на `301d7ee` — 98,4%; на GitHub ещё не запускался | `.github/workflows/ci.yml:114-133`, `apps/api/pyproject.toml:107-110` |
| Алерты на всплеск неудачных входов и команд машинам | сделано: счётчик с порогом и warning в логе | «Аудит и мониторинг», ADR-0029 |
| Отчёт `docs/10-security-review.md` | этот документ; дополнить после стенда и CI | — |

## Таблица по правилам

Статусы: **выполнено**; **исправлено в ветке** — было нарушено или не проверялось тестом, исправлено коммитом
этой ветки; **исключение пилота** — ослаблено намеренно, это записано в docs или ADR; **перед боевым запуском** —
docs/03 сам откладывает; **пробел** — не выполнено или не проверяется по репозиторию.

Всего 89 правил: выполнено — 61, исправлено в ветке — 18, исключение пилота — 4, перед боевым запуском — 4,
пробелов — 2.

### Аутентификация и сессии

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| OTP клиента — 6 цифр | выполнено | Тест. Код из `secrets.randbelow`, ведущие нули сохраняются | `A/identity/domain.py:30`, `A/identity/tests/test_domain.py::test_otp_codes_are_six_digits_with_leading_zeros_kept` |
| OTP живёт 5 минут | выполнено | Тест на Postgres: просроченный код отклоняется, `expires_in` равен 300 | `A/identity/domain.py:31`, `A/identity/tests/test_sign_in.py::test_expired_code_is_rejected` |
| Не больше 5 попыток на код | выполнено | Тест: пятая ошибка сжигает код. Строка кода блокируется `FOR UPDATE`, параллельные попытки счётчик не обходят | `A/identity/tests/test_sign_in.py::test_code_is_burnt_after_five_wrong_attempts`, `A/identity/repository.py:43` |
| Лимиты запроса OTP на номер и IP | выполнено | Тесты на 429: номер — раз в 60 с и 5 в час, IP — 20 в час. На стенде IP берётся из `X-Forwarded-For` только от Caddy | `A/identity/tests/test_sign_in.py::test_codes_per_phone_are_limited_per_hour`, `::test_codes_per_ip_are_limited_per_hour`, `infra/stand/compose.yml:49` |
| Лимиты проверки OTP на номер и IP | выполнено | Тесты: номер — 10 в час, IP — 50 в час. Лимит по номеру считается только при живом коде, чтобы посторонний не закрывал вход | `A/identity/tests/test_sign_in.py::test_guesses_per_phone_are_limited_across_codes`, `::test_guesses_per_ip_are_limited_per_hour` |
| `SMS_BACKEND=fixed`: входят только заведённые номера | исключение пилота | Тест: другой номер получает 403, лимиты считаются до этой проверки. Исключение описано в ADR-0009 | `A/identity/tests/test_pilot_sign_in.py::test_other_phones_cannot_get_a_code`, `::test_probing_for_test_phones_is_rate_limited` |
| `fixed`: код случайный, угадываемые отклоняются | исправлено в ветке | Тест. Раньше отклонялись только коды из одной цифры и подряд идущие, `121212`, `112233`, `000001` принимались. `c83c021`: нужно не меньше четырёх разных цифр, подряд идущие (`123456`, `987654`) по-прежнему отклоняются. Шаблоны env генерируют только допустимые коды. Случайность значения на том, кто заполняет env. Код в `stand.env` на сервере, если его выбрали до правила, может не подойти — проверить перед первой выкладкой (docs/08) | `A/core/config.py:413-418`, `apps/api/tests/test_config.py::test_fixed_sms_refuses_guessable_codes`, `infra/stand/stand.env.example:32-33`, `docs/08-demo-stand.md:91-97` |
| `fixed`: код не отправляется и не пишется в лог | исключение пилота | Тест. `SMS_BACKEND=log` (пишет код в лог) запрещён при `API_ENV=stand` и `prod` | `A/identity/tests/test_pilot_sign_in.py::test_the_code_never_reaches_the_log`, `apps/api/tests/test_config.py::test_sms_codes_in_the_log_are_refused_on_a_public_server` |
| `fixed`: попытки и лимиты те же, с реальными клиентами не использовать | исключение пилота | Тест на общие попытки. Запрета для реальных клиентов в коде нет: `fixed` разрешён при `API_ENV=prod`, это организационное правило. Замена шлюзом и запрет в конфигурации — в чек-листе docs/03 «Перед боевым запуском» | `A/identity/tests/test_pilot_sign_in.py::test_wrong_codes_spend_the_attempts`, `apps/api/tests/test_config.py::test_fixed_sms_is_allowed_in_prod`, `docs/03-security.md:115-116` |
| Пароль сотрудника — argon2id | выполнено | Тест: хэш начинается с `$argon2id$`. Для неизвестного email сверка с пустышкой выравнивает время ответа | `A/identity/tests/test_staff_domain.py::test_password_hash_is_argon2id_and_verifies`, `::test_dummy_hash_never_matches_a_guess` |
| Обязательная 2FA (TOTP) сотрудника | выполнено | Тесты: после пароля выдаётся только challenge, сессия — только после верного TOTP. Окно до первого входа — в «Находках» | `A/identity/service.py:575-638`, `A/identity/tests/test_staff_sign_in.py::test_later_sign_in_needs_a_fresh_code` |
| Access-токен — 15 минут | выполнено | Тест. Алгоритм HS256 закреплён, `alg=none` и подмена отклоняются | `A/identity/tests/test_domain.py::test_access_token_lives_fifteen_minutes`, `::test_forged_access_tokens_are_rejected` |
| Refresh ротируется при каждом обновлении | выполнено | Тесты для клиента и сотрудника | `A/identity/tests/test_sign_in.py::test_refresh_rotates_the_token`, `A/identity/tests/test_staff_sign_in.py::test_refresh_rotates_the_cookie_and_logout_ends_the_session` |
| В БД только хэш refresh | выполнено | Тест для клиентов. У сотрудников та же функция хэширования (ревью кода), отдельного теста нет | `A/identity/tests/test_sign_in.py::test_only_hashes_are_stored`, `A/identity/service.py:718` |
| Повтор старого refresh в живой сессии → отзыв сессий устройства | выполнено | Тесты. Оговорка: повтор в первые 30 с после ротации даёт 401 без отзыва (две вкладки, ADR-0009). В docs/03 это дописано в `301d7ee` | `A/identity/tests/test_sign_in.py::test_reused_refresh_token_revokes_the_device_sessions`, `A/identity/domain.py:39`, `docs/03-security.md:29-31` |
| Повтор в закончившейся сессии → 401 без отзыва | выполнено | Тесты на отозванную и на истёкшую сессию | `A/identity/tests/test_sign_in.py::test_replay_from_a_revoked_session_leaves_the_new_session_alone`, `::test_replay_from_an_expired_session_revokes_nothing` |
| Мобильные: токены только в `expo-secure-store` | выполнено | Тест jest. Refresh — с `WHEN_UNLOCKED_THIS_DEVICE_ONLY`, access — в памяти. AsyncStorage в `apps/mobile/src` нет | `apps/mobile/src/features/auth/session/tokenStore.ts`, `apps/mobile/src/features/auth/session/tokenStore.test.ts` |
| Веб клиента: refresh в cookie, access в памяти | выполнено | Тест API и jest: cookie `HttpOnly; Secure; SameSite=Strict`, в теле ответа `refresh_token` пуст | `A/identity/tests/test_sign_in.py::test_web_client_gets_the_refresh_token_only_in_a_cookie`, `apps/mobile/src/features/auth/session/tokenStore.web.ts` |
| Веб админки: refresh в cookie, access в памяти | выполнено | Тест API и vitest. Хранилищ браузера в `apps/admin/src` нет | `A/identity/tests/test_staff_sign_in.py::test_refresh_token_comes_only_in_a_strict_cookie`, `apps/admin/src/auth/session.test.ts` |

### Авторизация

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Проверка прав на каждом эндпоинте общими зависимостями | выполнено | Новая матрица доступа (`7fddb2d`): таблица ролей написана вручную, 401 и 403 проверяются без БД, маршрут без записи роняет тест. Ручных проверок роли в `api.py` нет | `apps/api/tests/test_route_access_matrix.py::test_every_route_is_listed`, `::test_a_staff_route_refuses_a_role_it_does_not_list`, `A/identity/dependencies.py:81-105` |
| Проверка на уровне объекта (IDOR) | исправлено в ветке | Сверка вручную по таблицам матрицы доступа: 72 маршрута с id объекта в пути (26 клиентских, 46 сотрудников), для каждого искали тест на чужой объект или чужую роль. Были тесты на чужие аренды, карты, документы, штрафы, переписки, инциденты, задачи. Недостающие дописаны в `c8d73f8`: коллега и оператор не по своей задаче, дочерний id другого родителя на каждой ссылке на фото в `support` и `maintenance`, живая сессия на разблокировке, принудительном завершении и повторе возврата, точные коды в тестах ролей. Чужой объект — 404, владельца проверяет фильтр в запросе репозитория. Матрица проверяет только роли: что у нового маршрута с объектом есть тест на чужой объект, автоматически не проверяется | `A/rentals/repository.py:106`, `A/kyc/tests/test_documents.py::test_another_client_never_sees_the_documents`, `A/support/tests/test_fines_api.py::test_a_photo_of_another_fine_is_not_found_under_ones_own` |
| Опасные действия сотрудника — с живой сессией | исправлено в ветке | Тесты. Начало задачи техника (`1d93275`) и одобрение KYC (`c87b6a8`) раньше принимали токен до 15 минут после отзыва | `A/maintenance/tests/test_tasks_api.py::test_taking_a_task_needs_live_session`, `A/kyc/tests/test_review.py::test_approval_needs_a_live_session` |
| Внешние id — UUID | выполнено | Ревью кода: все `*_id` в путях типизированы UUID, автоинкремента в ключах внешних сущностей нет. Автотеста нет | `A/rentals/api.py`, `A/rentals/models.py` и так же в остальных модулях |
| «Открыть»: активная аренда этой машины | выполнено | Тест. Id машины клиент не передаёт: команда идёт на машину из его аренды | `A/rentals/schemas.py:317-324`, `A/rentals/tests/test_commands_api.py::test_a_cancelled_rental_takes_no_commands` |
| «Открыть»: KYC одобрен, проверка при каждой команде | выполнено | Тест: заявку вернули на проверку — 403, машине ничего не отправлено | `A/rentals/service.py:787-789`, `A/rentals/tests/test_commands_api.py::test_a_client_whose_documents_were_revoked_cannot_open` |
| «Открыть»: клиент не заблокирован | выполнено | Тест | `A/rentals/tests/test_commands_api.py::test_a_blocked_client_cannot_open` |
| «Открыть»: не дальше 50 м | выполнено | Тест: ровно 50 м открывает, дальше — 409. Без свежей телеметрии не открывает | `A/rentals/domain.py:417-429`, `A/rentals/tests/test_commands_api.py::test_the_reach_of_the_car_is_fifty_meters` |
| Заблокированному во время поездки — только «закрыть» | исправлено в ветке | Тесты. До `c2c0669` закрыть машину не давали, и она оставалась открытой. KYC проверяется и здесь, см. «Находки» | `A/rentals/domain.py:388-394`, `A/rentals/tests/test_commands_api.py::test_a_client_blocked_during_the_trip_can_close_the_car` |

### Деньги и бизнес-логика

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Стоимость считает только сервер | выполнено | Ревью кода: в клиентских входных схемах нет сумм и тарифов. Суммы принимают только маршруты admin и operator. Лишние поля отвергает `extra="forbid"`. Отдельного теста нет | `A/pricing/schemas.py:38`, `A/rentals/schemas.py:604`, `apps/api/tests/test_route_access_matrix.py:87-98` |
| Платёжные операции идемпотентны | исправлено в ветке | Тесты. Ключ уникален в БД, серверные ключи выводятся детерминированно. Заголовок `Idempotency-Key` обязателен у корректировки и ручного штрафа. Тест на его отсутствие был только у штрафа, у корректировки добавлен в `0d2fa40` | `A/payments/models.py:138`, `A/payments/tests/test_operations.py::test_a_repeated_call_returns_the_same_operation`, `A/support/tests/test_fines_api.py::test_a_manual_fine_needs_an_idempotency_key`, `A/rentals/tests/test_staff_api.py::test_an_adjustment_needs_an_idempotency_key` |
| Вебхуки платёжки с проверкой подписи | выполнено | Тесты на неверную подпись, чужой секрет, старую метку, повтор. В пилоте есть только фейковый провайдер | `A/core/signing.py:44-56`, `A/payments/tests/test_cards_api.py::test_webhook_with_a_bad_signature_is_refused` |
| Корректировка суммы сотрудником | выполнено | Тесты: без комментария — 422, чек не меняется, уменьшение возвращает деньги через `payments`. Увеличение списывается сразу и становится долгом только при отказе (так в ADR-0026). Неизменность держат триггеры БД | `A/rentals/tests/test_staff_api.py::test_a_decrease_refunds_the_paid_rental_and_keeps_the_receipt`, `::test_a_declined_increase_is_a_debt_the_client_pays_later` |
| Принудительное завершение и блокировка | выполнено | Тесты: только operator и admin, причина обязательна, живая сессия, в аудите причины нет. Причина завершения лежит в столбце `rentals.force_finish_reason`, а не в своей таблице (так решено в ADR-0026). docs/03 уточнён в `301d7ee` | `A/rentals/tests/test_staff_api.py::test_a_forced_finish_needs_a_reason`, `A/rentals/tests/test_clients_staff_api.py::test_a_block_needs_a_reason`, `A/rentals/models.py:120`, `docs/03-security.md:48-50` |

### Данные

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Входные схемы с `extra="forbid"` | исправлено в ветке | Тесты. Разовый обход скриптом нашёл 52 модели тела и query, включая вложенные, — все `forbid`. Теперь это проверяет мета-тест (`0d2fa40`): новая модель без `forbid` роняет его. Скалярные query-параметры лишние ключи не отвергают; docs/03 теперь ограничивает правило телами и query-моделями (`301d7ee`) | `apps/api/tests/test_input_schemas.py::test_every_input_model_forbids_unknown_fields`, `A/identity/tests/test_validation.py::test_unknown_fields_are_rejected`, `docs/03-security.md:53-55` |
| То же для входов вне маршрутов | выполнено | Ревью кода: первое сообщение WebSocket, телеметрия, вебхук — строгие схемы | `A/fleet/schemas.py:35`, `A/fleet/schemas.py:315`, `A/payments/fake_provider.py:93` |
| SQL только с параметрами | выполнено | Настройка: правило ruff `S608` и Semgrep с `--error`. Все `text()` статичны, подавлений нет | `apps/api/pyproject.toml:61-62`, `.github/workflows/ci.yml:249-266` |
| Фото документов — приватный бакет | выполнено | Тест: прямая ссылка без подписи — 403 | `A/kyc/tests/test_documents.py::test_the_bucket_is_private`, `infra/docker-compose.yml:47-50` |
| Подписанные ссылки — не дольше 5 минут | выполнено | Тест: `expires_in` равен 300, после срока ссылка не работает | `A/kyc/domain.py:11`, `A/kyc/tests/test_documents.py::test_a_signed_link_stops_working_after_its_ttl` |
| Документы видят владелец и операторы KYC | выполнено | Тесты. Отдельной роли нет: это operator и admin, техник отсечён. Каждая ссылка сотруднику — в аудите | `A/kyc/tests/test_review.py::test_technicians_do_not_see_the_queue`, `::test_a_document_link_opens_the_photo_and_is_audited` |
| Тип файла по содержимому | выполнено | Тест: PNG, объявленный как JPEG, принят и сохранён как JPEG. Все модули идут через `clean_photo` | `A/core/photos.py:77`, `apps/api/tests/test_photos.py::test_anything_but_a_jpeg_png_or_webp_is_refused` |
| Лимит размера фото | выполнено | Тесты: 10 МБ и 30 Мп, загрузка частями обрезается на лимите | `A/core/photos.py:13-16`, `apps/api/tests/test_photos.py::test_a_file_over_the_size_limit_is_refused`, `A/kyc/tests/test_documents.py::test_a_chunked_upload_is_cut_off_at_the_limit` |
| Удаление EXIF | выполнено | Тесты функции и объекта в хранилище | `apps/api/tests/test_photos.py::test_metadata_is_gone_from_a_jpeg`, `A/kyc/tests/test_documents.py::test_the_stored_photo_is_a_jpeg_without_metadata` |
| Фото и ссылки не кэшируются | выполнено | Тесты. `no-store` входит в подпись ссылки, без него — 403. Все 11 маршрутов со ссылками на фото отвечают с `no-store` | `A/core/storage.py:81`, `A/kyc/tests/test_documents.py::test_the_no_store_parameter_is_signed` |
| Фото чата и инцидентов | выполнено | Тесты. Ссылка сотруднику пишется в аудит до выдачи. Срок 5 минут проверен тестом только для чата | `A/support/tests/test_incidents_api.py::test_staff_photo_links_are_audited`, `A/support/tests/test_chat_api.py::test_a_photo_of_another_conversation_is_not_found_under_this_one` |
| Фото осмотров для сотрудников | выполнено | Тесты. Лимит 600 ссылок в час на сотрудника добавлен в `ac98aa3` | `A/rentals/tests/test_staff_api.py::test_a_photo_link_for_staff_is_in_the_audit_log`, `::test_photo_links_are_limited_per_staff_member` |
| Фото задач техников | выполнено | Тесты: ссылки в аудите, коллега не по своей задаче ссылку не получает. Загрузка фото пишется в аудит с `366bbd4` — см. «Аудит» | `A/maintenance/tests/test_tasks_api.py::test_assignee_adds_photos_and_staff_get_short_links`, `::test_a_colleague_gets_no_link_to_a_photo_of_the_task`, `::test_every_photo_a_technician_adds_is_audited` |
| Фото штрафов | выполнено | Тесты: `fine.photo_viewed`, чужой штраф — 404, загрузка — 60 в час (тест на 429 — `7ae8678`) | `A/support/tests/test_fines_api.py::test_every_photo_an_operator_opens_is_audited`, `::test_photo_uploads_are_limited_per_staff_member` |
| ПД не пишутся в логи | исправлено в ветке | Тесты по потокам: вход, KYC, поток карты, команды, уведомления. Параметры SQL скрыты. Центрального маскирования не было; `54e3c09` добавил общий процессор для structlog и записей библиотек: значения полей из явного списка (телефон, email, пароли, токены, номер документа, заголовки авторизации) заменяются на `[redacted]`. Текст события и строки ошибок он не смотрит, см. «Находки» | `A/core/logging.py:11-47`, `apps/api/tests/test_logging.py::test_personal_data_and_secrets_are_redacted_from_logs`, `A/core/db.py:31-35`, `A/identity/tests/test_sign_in.py::test_phones_codes_and_tokens_stay_out_of_the_logs`, `A/kyc/tests/test_documents.py::test_keys_and_links_stay_out_of_the_logs` |
| IP и query-строки не попадают в логи Caddy и uvicorn | исправлено в ветке | Тесты и проверка Caddy в CI. `79c8a4e`: Caddy убирает query, IP и заголовки. `b394746`: uvicorn не пишет адрес клиента при WebSocket-рукопожатии. Query в этой строке остаётся. После `80b0368` скрипт проверяет только фильтр access-лога сайтов: до логгера `default` строки с запросом больше не доходят, его фильтр без проверки. Оба — см. «Находки» | `infra/stand/web/Caddyfile:11-21`, `infra/stand/web/Caddyfile:50-63`, `scripts/check-caddy.mjs:170-204`, `apps/api/tests/test_logging.py::test_websocket_handshake_is_logged_without_the_client_address`, `apps/api/tests/test_middleware.py::test_access_log_is_json_with_request_id_and_without_query_string` |
| Минимизация: храним только нужное | выполнено | Ревью кода. Это принцип, проверены видимые меры: ключи фото без id клиента, заменённое фото удаляется, метаданные вычищаются, сессии и уведомления удаляются по сроку. Перечня ПД по полям нет | `A/kyc/tests/test_documents.py::test_a_new_upload_replaces_the_photo_and_deletes_the_old_one`, `A/rentals/tests/test_inspection_api.py::test_a_photo_is_stored_clean_under_a_key_without_the_client` |
| Сроки хранения документов — с юристом | перед боевым запуском | Не проверялось, ждёт юриста. Удаления по сроку нет для фото и журнала аудита. Есть для сессий (7 дней) и уведомлений (30 дней). Сроки и задача удаления внесены в чек-лист docs/03 «Перед боевым запуском» (`301d7ee`) | `docs/03-security.md:71`, `docs/03-security.md:117-118`, `docs/adr/0010-audit-log.md:99` |

### Транспорт и заголовки

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Только HTTPS | выполнено | Ручной запуск Caddy со стендовым Caddyfile: HTTP отвечает 308 на HTTPS. Наружу открыт только `web`. Автотеста на редирект нет | `infra/stand/compose.yml:75-78`, `infra/stand/web/Caddyfile:76-111` |
| HSTS в ответах API | выполнено | Тест для `API_ENV=prod`. В dev и test HSTS выключен, при старте пишется предупреждение | `A/core/middleware.py:101-102`, `apps/api/tests/test_middleware.py::test_prod_enables_hsts_and_hides_the_docs` |
| HSTS и общие заголовки за Caddy | выполнено | Проверка `check-caddy.mjs` в CI, локально проходит. Caddy ставит HSTS на 1 год и заменяет им двухлетний HSTS API: заглушка в скрипте отдаёт заголовки API, дубль роняет проверку | `infra/stand/web/Caddyfile:24-42`, `scripts/check-caddy.mjs:146-162` |
| Заголовки на собственных ответах Caddy (502, 404) | исправлено в ветке | Проверка в CI, локально проходит. До `80b0368` 502 и 404 шли без HSTS, `nosniff`, `Referrer-Policy` и с `Server: Caddy`, а скрипт статусы не 200 пропускал. Теперь заголовки ставятся до обработчиков и ещё раз отложенно, `handle_errors` убирает `Server` и дописывает причину ошибки в строку access-лога. Скрипт проверяет пять ответов, среди них 502 и два 404 | `infra/stand/web/Caddyfile:30-49`, `scripts/check-caddy.mjs:36-48`, `scripts/check-caddy.mjs:152-167`, `scripts/check-caddy.mjs:187-194` |
| CORS — явный список доменов | выполнено | Тесты конфигурации: `*`, адреса без схемы и с путём отклоняются, по умолчанию список пуст. Тесты middleware: заданный origin получает CORS-заголовки, чужой — нет. На стенде список — `https://app.` и `https://admin.` своего домена; админка ходит в API со своего origin через Caddy, ей CORS не нужен. Caddy заголовки `Access-Control-*` не трогает (ревью Caddyfile), но `check-caddy.mjs` CORS не проверяет: ответ через Caddy проверить на стенде, см. «Что ждёт стенда» | `A/core/config.py:282-292`, `apps/api/tests/test_config.py::test_cors_rejects_anything_but_explicit_origins`, `apps/api/tests/test_middleware.py::test_cors_allows_configured_origin`, `::test_cors_rejects_unknown_origin`, `infra/stand/compose.yml:53`, `infra/stand/web/Caddyfile:74-75` |
| `nosniff`, `Referrer-Policy`, `frame-ancestors 'none'` в API | выполнено | Тест. CSP `default-src 'none'; frame-ancestors 'none'`. Исключение — только `/docs`, на stand и prod его нет | `A/core/middleware.py:89-102`, `apps/api/tests/test_middleware.py::test_security_headers_are_set` |
| CSP для админки | исправлено в ветке | Проверка в CI. CSP задаёт Caddy, `frame-ancestors 'none'` есть у `admin.` и `app.`. Раньше автотеста не было, у `s3.` CSP не было. `80b0368`: скрипт проверяет `frame-ancestors 'none'` у `admin.` и `app.`, у `s3.` — точное `default-src 'none'; frame-ancestors 'none'` | `infra/stand/web/Caddyfile:84`, `infra/stand/web/Caddyfile:94`, `infra/stand/web/Caddyfile:109`, `scripts/check-caddy.mjs:35-48` |
| Rate limiting: вход и OTP | исправлено в ветке | Тесты на 429 для всех 9 лимитов. Два лимита сотрудников по IP (30 в час) были без теста, тесты добавлены в `0d2fa40` | `A/identity/tests/test_staff_sign_in.py::test_password_guessing_is_rate_limited`, `::test_totp_guessing_is_rate_limited`, `::test_passwords_from_one_address_are_limited_across_emails`, `::test_codes_from_one_address_are_limited_across_staff` |
| Rate limiting: команды машине | исправлено в ветке | Тесты. Принудительное завершение слало команду мимо лимита сотрудника 20 в минуту, исправлено в `b15712e` | `A/rentals/tests/test_staff_api.py::test_a_forced_finish_counts_against_the_staff_command_limit`, `A/fleet/tests/test_commands_api.py::test_commands_are_rate_limited` |
| Rate limiting: платежи и карты | исправлено в ветке | Тесты. Денежные действия сотрудника лимита не имели, теперь 30 в минуту (`3d39dcd`). Тест на 429 лимита бронирования (20 в час) добавлен в `0d2fa40` | `A/payments/service.py:79-89`, `A/rentals/tests/test_staff_api.py::test_money_actions_are_limited_per_staff_member`, `A/payments/tests/test_cards_api.py::test_binding_is_rate_limited`, `A/rentals/tests/test_reservations_api.py::test_reservations_are_rate_limited` |
| Rate limiting: сообщения в поддержку | выполнено | Тесты: клиент — 20 в минуту, сотрудник — 30 в минуту | `A/support/tests/test_chat_api.py::test_messages_are_rate_limited`, `::test_staff_answers_are_rate_limited` |
| Rate limiting: загрузка фото | выполнено | Тесты на 429 для всех пяти видов загрузки, часть добавлена в `7ae8678` | `A/kyc/tests/test_documents.py::test_uploads_are_rate_limited`, `A/maintenance/tests/test_tasks_api.py::test_photo_uploads_are_limited_per_staff_member` |
| Rate limiting: инциденты | выполнено | Тест: 10 в час | `A/support/tests/test_incidents_api.py::test_incident_reports_are_rate_limited` |
| Вебхук платёжки: HMAC и метка времени | выполнено | Тесты: окно 300 с, повтор отсекается по `event_id`. Адаптеру настоящего провайдера проверку придётся сделать заново | `A/core/signing.py:44-58`, `A/payments/tests/test_fake_provider.py::test_old_webhook_is_refused` |
| Вебхук телематики: HMAC и метка времени | выполнено | Тесты API. Секреты телеметрии и команд разные. Тесты эмулятора не запускались, проверены по коду | `A/fleet/tests/test_telemetry_api.py::test_repeated_batch_is_ignored`, `services/car-emulator/tests/test_signing.py::test_timestamp_outside_tolerance_fails` |

### Секреты

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Локальный `.env` в `.gitignore` | исправлено в ветке | Настройка, `git check-ignore`. Раньше не игнорировались `infra/stand/stand.env`, `.env.development`, `.env.test`. `73af880`: игнорируются `.env`, `.env.*` и `*.env`, кроме `.env.example`; все пять шаблонов в git, ни одного другого env-файла в индексе нет | `.gitignore:1-5` |
| Шаблоны `.env.example` без значений | выполнено | Ревью: все секретные переменные пусты во всех пяти шаблонах. Значения заданы только у несекретных настроек (порты, `API_ENV`, имя БД, провайдеры-заглушки). В шаблонах админки и мобильного приложения секретов нет вовсе: адреса API и стиля карты и `EAS_PROJECT_ID`, все пустые | `.env.example`, `apps/admin/.env.example`, `apps/mobile/.env.example`, `infra/stand/stand.env.example`, `tests/load/stand.env.example` |
| В облаке — секреты GitHub или сервера | выполнено | Ревью workflow: секреты из environment `stand` и из `stand.env` на сервере. Сами секреты ещё не заведены: стенд не выкладывался | `.github/workflows/stand.yml:73-100`, `docs/08-demo-stand.md:28-37` |
| gitleaks в CI | выполнено | Ручной запуск того же образа и команды по всей истории на `301d7ee`: 124 коммита, утечек нет. В CI ветки ещё не запускался, на `main` job зелёный | `.github/workflows/repo-checks.yml:12-22` |

### Зависимости и CI

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Dependabot для pip, npm и Actions | выполнено | Настройка: еженедельно, плюс docker. Образ стенда `infra/stand/web` (Node и Caddy) добавлен в `73af880`. Не покрыты образы из docker-compose (Postgres, Redis, SeaweedFS). Dependabot alerts выключены | `.github/dependabot.yml:93-101` |
| `pip-audit` в CI | выполнено | Ручной запуск той же командой во всех трёх Python-каталогах на `301d7ee`: уязвимостей нет. В CI ветки ещё не запускался | `.github/workflows/ci.yml:135-153` |
| `npm audit` (high и critical) | выполнено | Ручной запуск `npm run audit` на `301d7ee`, уже с проверкой allowlist из `a97e5ff`: неодобренных high и critical нет, allowlist пуст. В CI ветки ещё не запускался | `scripts/npm-audit.mjs`, `.github/workflows/ci.yml:178-181` |
| Исключения `npm audit` | исправлено в ветке | Тест скрипта. Две устаревшие записи удалены, устаревшая запись роняет CI (`b42257b`). `a97e5ff`: запись без id вида `GHSA-*`, пакета или причины, с неверной датой или со сроком `reviewBy` дальше 92 дней роняет CI, даже пока её уязвимость не выдаётся | `scripts/npm-audit.mjs:18-67`, `scripts/tests/npm-audit.test.sh`, `.github/npm-audit-allowlist.json` |
| PR с исключением `npm audit` — отдельный пункт для ревьюера | исправлено в ветке | В шаблоне PR не было такого пункта, правило держалось на памяти автора. Пункт добавлен в чек-лист шаблона (`e4918c1`). Сейчас allowlist пуст, и эта ветка исключений не добавляет | `docs/03-security.md:91`, `.github/pull_request_template.md:18` |
| Semgrep в CI | выполнено | Ручной запуск той же версии и теми же наборами правил на снимке `301d7ee` (`git archive`): 645 файлов, 0 находок; исходники нагрузочного теста отдельно, как в CI: 18 файлов, 0 находок. В CI ветки ещё не запускался | `.github/workflows/ci.yml:249-268` |
| gitleaks в CI | выполнено | См. «Секреты» | `.github/workflows/repo-checks.yml:12-22` |
| Зафиксированные версии | выполнено | Настройка: `uv sync --locked`, `npm ci`, actions закреплены по SHA | `apps/api/uv.lock`, `package-lock.json`, `.github/workflows/ci.yml:102` |

### Мобильное приложение

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Никаких секретов в коде приложения | выполнено | Ревью и gitleaks: в бандл попадают только адрес API и адрес стиля карты, оба публичные. Теста на собранный бандл нет | `apps/mobile/.env.example`, `apps/mobile/app.config.ts:22-29` |
| Certificate pinning | перед боевым запуском | Не реализовано | `docs/03-security.md:96`, `docs/03-security.md:114` |
| Обнаружение root/jailbreak | перед боевым запуском | Не реализовано | `docs/03-security.md:96`, `docs/03-security.md:114` |

### Аудит и мониторинг

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| Журнал аудита только на вставку | выполнено | Тест: UPDATE, DELETE и TRUNCATE отклоняют триггеры БД, для любой роли | `apps/api/migrations/versions/20261023_4a250ed5a850_add_audit_events.py:64-91`, `A/audit/tests/test_audit_log.py::test_database_rejects_changing_events` |
| Роль БД без права владения журналом | исключение пилота | Ревью: приложение подключается как владелец и может снять триггер. Разделение ролей отложено ADR-0010 до боевого запуска | `docs/adr/0010-audit-log.md:84-85`, `docs/adr/0010-audit-log.md:96-98` |
| В аудите — команды машинам | пробел | Тесты: все пути команд идут через два места записи `car.command_sent`. Но запись делается после отправки и в отдельной транзакции: если БД упадёт между ними, команда дойдёт до машины без записи в аудите. Нужно решение, см. «Находки» | `A/fleet/service.py:384`, `A/fleet/service.py:394-402`, `A/fleet/tests/test_commands_api.py::test_a_done_command_is_audited` |
| В аудите — денежные операции | выполнено | Тест: каждый исход операции у провайдера пишется в той же транзакции | `A/payments/service.py:541-556`, `A/payments/tests/test_operations.py::test_every_known_outcome_goes_to_the_audit_log` |
| В аудите — действия сотрудников | исправлено в ветке | Ревью: изменяющие маршруты сотрудников сверены с аудитом. Загрузка фото задачи техником не писалась; `366bbd4` добавил `maintenance_task.photo_added` в той же транзакции, только с id фото, и подпись в админке. Общего теста на все маршруты нет | `A/maintenance/service.py:400-410`, `A/audit/domain.py:66`, `A/maintenance/tests/test_tasks_api.py::test_every_photo_a_technician_adds_is_audited` |
| Алерты на аномалии | исправлено в ветке | Тесты. `61b6e3e` добавил счётчики, `f25de50` считает команду и при сбое записи в БД и переименовал метрики в `car-commands-all-staff` и `car-commands-all-clients`. Пороги совпадают с docs/03. Сбой Redis не превращается в 500 | `A/core/alerts.py`, `A/fleet/service.py:345-346`, `apps/api/tests/test_alerts.py::test_warns_once_when_the_window_reaches_the_threshold`, `A/fleet/tests/test_commands_api.py::test_commands_the_car_got_count_even_if_recording_them_fails`, `docs/adr/0029-security-spike-warnings.md` |
| Внешние оповещения | перед боевым запуском | Не реализовано, в пилоте только warning в логе API | `docs/03-security.md:105`, `docs/03-security.md:119` |

### Процесс

| Правило | Статус | Как проверено | Где |
|---|---|---|---|
| PR в `identity`, `kyc`, `payments`, `rentals` и командах машине — `/security-review` и ревью человеком | пробел | Для этой ветки ещё не выполнено: она затрагивает все эти модули, а PR не открыт. В шаблоне PR есть пункт про `/security-review`, про ревью человеком — нет. Защиту ветки `main` API для этой учётной записи не показывает (404), так что обязательность ревью по репозиторию не проверена. Сделать до слияния и сослаться здесь на PR | `docs/03-security.md:123-124`, `.github/pull_request_template.md:17` |

## Находки и решения

Находки первого прохода, закрытые коммитами ветки (заголовки Caddy, аудит фото задач, маскирование логов, коды
SMS, `.gitignore`, мета-тест, тесты на 429, проверка allowlist, docs/03), перечислены в таблице со статусом
«исправлено в ветке». Здесь — решения и то, что осталось.

### Решения, принятые человеком

- **Живая сессия в KYC — только на одобрение.** Одобрение сразу допускает клиента к аренде (`A/kyc/api.py:196-197`).
  Отклонение, очередь, просмотр документов, правки цен, зон и чата работают по токену. Новая роль или отключение
  сотрудника там действуют в течение 15 минут, это принятый остаток
  [ADR-0011](adr/0011-staff-auth-password-totp.md) (строки 62-65).
- **Лимит денежных действий сотрудника — 30 в минуту** (`A/payments/service.py:79-89`). Значение не измерено,
  это начальная оценка. Общий счётчик на корректировки, повторы возвратов, ручные штрафы и их отмену.
- **Заблокированному клиенту можно закрыть машину во время поездки** (`c2c0669`, ADR-0026). Закрытие не даёт
  доступа в машину.
- **Пороги алертов** — по [ADR-0029](adr/0029-security-spike-warnings.md): неудачные входы клиентов 20 за 5 минут,
  сотрудников 8 за 5 минут, команды сотрудников 40 за 5 минут, клиентов 120 в минуту.

### Принятые остатки (код не меняем)

- **422 на битый JSON раньше проверки прав.** FastAPI разбирает JSON-тело до зависимостей. Поэтому аноним узнаёт,
  какие 32 маршрута принимают JSON-тело. Схема и данные не раскрываются. Список закреплён в
  `apps/api/tests/test_route_access_matrix.py` (`UNPARSED_JSON_ROUTES`, тест
  `test_only_the_listed_routes_judge_unparsed_json_ahead_of_the_caller`): новый такой маршрут роняет тест.
- **У обновления refresh-токена нет лимита.** Токен случайный, 256 бит, перебор бесполезен. docs/03 называет
  «вход, OTP», refresh там нет.
- **Удаление карты и выбор карты по умолчанию без лимита** (`A/payments/api.py:138`, `:151`). Провайдер не
  вызывается, денег это не двигает.
- **`Permissions-Policy`, COOP и CORP не заданы.** docs/03 их не требует. Если ZAP отметит их на стенде, это уйдёт
  в бэклог.
- **HSTS на 1 год у Caddy и на 2 года у API.** На `api.` Caddy заменяет значение API своим, `check-caddy.mjs` это
  проверяет. Оба значения подходят.
- **Окно подключения чужой 2FA до первого входа сотрудника** и после `reset-totp`. Код не меняем. Процедура из
  ADR-0011 (строки 124-127): временный пароль — только через менеджер паролей, первый вход — сразу.
- **Окно 30 секунд после ротации refresh.** Повтор в это время даёт 401 без отзыва, иначе ломаются две вкладки
  (ADR-0009, строки 35-38). Теперь это записано и в docs/03 (`301d7ee`).
- **Причина принудительного завершения — столбец `rentals.force_finish_reason`**, а не своя таблица. Так решено в
  ADR-0026. В аудит причина не попадает, тест это проверяет. Формулировка docs/03 уточнена (`301d7ee`).
- **`SMS_BACKEND=fixed` разрешён при `API_ENV=prod`** (тест `test_fixed_sms_is_allowed_in_prod`). Запрет на
  реальных клиентов остаётся организационным, замена шлюзом — в чек-листе docs/03 и в «Отложено».
- **Скалярные query-параметры** на 7 маршрутах (например, `GET /support/messages`: `after`, `limit`) молча
  игнорируют лишние ключи. Утечки это не даёт. docs/03 теперь ограничивает правило `extra="forbid"` телами и
  query-моделями (`301d7ee`), мета-тест проверяет именно их.
- **Маскирование логов ловит только поля с известными именами.** Процессор `_redact_personal_data`
  (`A/core/logging.py:37-47`) смотрит ключи из списка `_REDACTED_KEYS` и один уровень вложенных словарей. Текст
  события, строки ошибок (`error=str(exc)`) и поля под другими именами он не проверяет, так что телефон в тексте
  исключения пройдёт. Это граница выбранного варианта, docs/03 описывает его как «страховку»: правило
  по-прежнему держится на «пишем только id» и тестах потоков. Новое имя поля с ПД нужно добавлять в список.

### Нужно решение

- **KYC отозван во время поездки — клиент не может закрыть машину командой (#166).** Решение по ADR-0026 касалось только
  блокировки: `even_if_blocked` снимает проверку блокировки, но не KYC (`A/rentals/service.py:787-791`,
  `:1042-1044`). Завершить аренду он может: `finish` KYC не проверяет и сам закрывает машину
  (`A/rentals/service.py:1174-1224`), но для этого нужна зона завершения и осмотр. Ещё машину закроет оператор
  принудительным завершением. Предложение: разрешить «закрыть» в поездке и при отозванном KYC, по тому же правилу,
  что и при блокировке.
- **Команда машине может остаться без записи в аудите**, если БД упадёт между отправкой команды и записью
  (`A/fleet/service.py:384`, `:394-402` для сотрудника; `:474-498` для клиента). Двухфазной фиксации с машиной
  нет. После `f25de50` такая команда хотя бы попадает в счётчик всплесков, но строки `car_command_sent` в логе у неё
  нет. Варианты: писать намерение до отправки и исход после, либо логировать `car_command_sent` в `finally`.
  Отдельная задача — #165.

### Предлагаем поправить небольшими правками

- **Фильтр `log default` в Caddyfile больше не проверяется.** После `80b0368` ошибки обрабатывает
  `handle_errors`, их причина уходит в строку access-лога сайта, и ни одна строка с запросом до логгера `default`
  не доходит. `check-caddy.mjs` проверяет только то, что видит в логе, поэтому поломка фильтра
  `infra/stand/web/Caddyfile:11-21` пройдёт незамеченной, а строки ошибки, которую не обработал `handle_errors`,
  понесут IP и query. Правка: в скрипте сравнить поля фильтра `default` с фильтром сайтов по JSON из
  `caddy adapt`, либо вызвать случай, который пишет в `default`.
- **Query-строка в строке WebSocket-рукопожатия.** uvicorn пишет путь вместе с query
  (`'%s - "WebSocket %s" [accepted]'`). Фильтр в `A/core/logging.py:60-75` убирает только адрес, общий процессор
  маскирования текст события не смотрит. Сейчас вреда нет: единственный WebSocket, `/admin/fleet/stream`,
  открывается без query, токен идёт в первом сообщении. Но то, что посторонний допишет в query, попадёт в лог.
  Правка: в том же фильтре отрезать `?…`, плюс случай в `apps/api/tests/test_logging.py`.
- **Образы docker-compose вне Dependabot.** Записи `docker` читают только Dockerfile. `postgis/postgis:16-3.6`,
  `redis:7.4.11-alpine`, `chrislusf/seaweedfs:4.48` (`infra/docker-compose.yml:9`, `:28`, `:46`, `:74`) и PostGIS
  стенда (`infra/stand/compose.yml:96`) никто не обновляет. Правка: записи экосистемы `docker-compose` для
  `/infra` и `/infra/stand` в `.github/dependabot.yml`.
- **`A/fleet/tests/test_stream.py:64-68`.** `_receive` ждёт сообщение в `ThreadPoolExecutor` с таймаутом, но выход
  из `with` ждёт зависший поток, так что пропавшее сообщение всё равно подвесит прогон. В матрице доступа то же
  сделано через поток-демон и закреплено тестом `test_a_silent_stream_fails_the_test_instead_of_hanging_the_run`.
  Перенести этот способ.

### Наблюдения о процессе

- **Возможно нестабильный тест** `A/support/tests/test_fines_api.py::test_a_manual_fine_needs_a_comment`. Один раз
  упал в длинном прогоне, отдельно проходит (прогнан повторно 6 ноября). Причина не найдена. Если повторится —
  завести задачу.
- **Три теста identity с `caplog`** дают ERROR при `-p no:logging` и проходят без этого флага. В CI флага нет.
- **Install-скрипты npm-пакетов не ограничены.** Политики `ignore-scripts` или списка разрешённых нет; в
  `package-lock.json` скрипты установки есть у `@parcel/watcher`, `fsevents`, `unrs-resolver`.
- **Свежесть Python-пакетов не закреплена в uv.** В Dependabot стоит выдержка 7 дней (`cooldown`), а у `uv lock`
  такого ограничения нет (`exclude-newer` не задан). `coverage` пришлось вручную закрепить на 7.17.0 (`1de45e5`).
- **Коммиты ветки ещё не проходили CI на GitHub.** Ветка не отправлена. Новые job `caddy` и `domain-coverage`,
  изменённые `check-caddy.mjs` и `npm-audit.mjs`, сканеры проверены только локально на Windows, все на `301d7ee`.
  После push сверить прогоны и сослаться на них здесь.

## Отложено до боевого запуска

Из таблицы (кроме ролей БД, всё это теперь есть в чек-листе docs/03 «Перед боевым запуском», `301d7ee`):

- certificate pinning и обнаружение root/jailbreak в мобильном приложении;
- внешние оповещения об аномалиях;
- сроки хранения документов, фото и журнала аудита и задача их удаления в `flagman-worker`;
- отдельные роли БД: приложение без права владения журналом аудита, миграции — своей ролью (ADR-0010);
- замена `SMS_BACKEND=fixed` настоящим SMS-шлюзом и запрет `fixed` в prod.

Из чек-листа docs/03 «Перед боевым запуском»:

- внешний пентест API, мобильного приложения и админки;
- юридическая проверка обработки ПД по Закону РБ № 99-З;
- модель угроз для реальной телематики;
- план реагирования на инциденты.

**Отдельные задачи с меткой `security`** (заведены 7 ноября 2026):

- **API и воркер ходят в БД суперпользователем (#167).** Оба берут `POSTGRES_USER` образа Postgres
  (`infra/docker-compose.yml:11`, `:115`, `:208`). `CREATE ROLE` и `GRANT` в `infra` и миграциях нет. Взлом API даёт
  полные права на БД. Тесно связано с ролями из ADR-0010.
- **Нет сканов по расписанию, Dependabot alerts выключены (#168).** Ни в одном workflow нет `schedule`. Новая уязвимость
  в зависимостях, которые никто не трогает, всплывёт только при следующем push. Dependabot alerts включает админ
  репозитория в настройках, в коде это не исправить.
- **ZAP с авторизацией (#169).** Скан без входа не видит админку за паролем и TOTP. Права и чужие объекты сейчас
  закрывают тесты, а не ZAP. Настройки ZAP в репозитории нет вообще.
- **Лимит тела запроса в Caddy (#170).** `request_body` в Caddyfile нет. Размер ограничен только у фото и телеметрии, у
  обычных JSON-маршрутов, включая анонимный `/auth/otp/request`, — нет. Порог должен быть выше 10 МБ, чтобы на
  большое фото отвечал сам API.
- **Команда машине без записи в аудите при сбое БД после отправки (#165)** — описание и варианты в «Нужно решение».

## Что ждёт стенда

Время согласовать с командой, не во время показа. Отчёты не кладём в репозиторий: он публичный, а в отчёте могут
быть ещё не исправленные находки. В этот документ записываем только число находок по уровням и что с ними сделано.
High исправляем в этой ветке, Medium и ниже заводим задачами в бэклог.

### Перед выкладкой: код SMS в `stand.env`

Если `stand.env` на сервере заполнили до правила о четырёх разных цифрах (`c83c021`), код `SMS_FIXED_CODE` может
ему не подходить, и тогда API не стартует. Перед первой выкладкой проверить его на сервере командой из
[docs/08](08-demo-stand.md) (раздел о выкладке, строки 91-97), при отказе — сгенерировать новый командой из
`infra/stand/stand.env.example` и раздать тем, кто показывает стенд.

### ZAP baseline по API и админке

Baseline только смотрит ответы и ничего не атакует. Образ ещё не скачивался: флаги проверить на первом запуске,
а digest образа записать сюда.

```bash
# digest закрепляем один раз и записываем в этот документ
docker buildx imagetools inspect ghcr.io/zaproxy/zaproxy:stable

# каталог вне репозитория; MSYS_NO_PATHCONV нужен в Git Bash, иначе он испортит /zap/wrk
mkdir -p ~/flagman-zap/<дата> && cd ~/flagman-zap/<дата>
for target in https://admin.<домен> https://app.<домен> https://api.<домен>/health; do
  name=$(echo "$target" | sed -E 's#https://([^./]+).*#\1#')
  MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/zap/wrk:rw" \
    ghcr.io/zaproxy/zaproxy@sha256:<digest> \
    zap-baseline.py -t "$target" -m 2 -J "$name.json" -r "$name.html"
done
```

Скан идёт без входа. На стенде `/docs` и `/openapi.json` отдают 404, поэтому по API baseline видит только
заголовки и ответы публичных маршрутов.

### CORS через Caddy

`check-caddy.mjs` CORS не проверяет, поэтому на стенде — preflight от своего и от чужого origin:

```bash
curl -si -X OPTIONS https://api.<домен>/health -H 'Origin: https://app.<домен>' \
  -H 'Access-Control-Request-Method: GET' | grep -i '^access-control-allow-origin'   # ждём https://app.<домен>
curl -si -X OPTIONS https://api.<домен>/health -H 'Origin: https://evil.example' \
  -H 'Access-Control-Request-Method: GET' | grep -ci '^access-control-allow-origin'  # ждём 0
```

### `load:stand`

По [docs/09](09-load-test.md): заполнить `tests/load/stand.env`, расширить `SMS_FIXED_PHONES` до 10 тестовых
номеров, запустить `npm run load:stand`. Итог записать в docs/09.

### Логи прогона на ПД и токены

Сразу после `load:stand` и ZAP, на сервере, функцией `stand` из [docs/08](08-demo-stand.md):

```bash
stand logs --no-color --since 2h > /tmp/run.log
grep -cE '\+?375[0-9]{9}' /tmp/run.log                          # телефоны
grep -cE 'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+' /tmp/run.log    # JWT
grep -ciE 'bearer |refresh_token|flagman_(staff_)?refresh=' /tmp/run.log
grep -ciE 'x-amz-(signature|credential)' /tmp/run.log           # подписанные ссылки на фото
grep -cE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' /tmp/run.log  # email
grep -cE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' /tmp/run.log         # IP: адреса 0.0.0.0 и внутренние Docker разобрать руками
grep -cE '"uri":"[^"]*\?' /tmp/run.log                          # query-строки в логе Caddy
grep -c 'Loadtest' /tmp/run.log                                 # имена тестовых клиентов
# общий SMS-код: значение берём из stand.env, чтобы оно не попало в историю shell
grep -cF -f <(grep '^SMS_FIXED_CODE=' stand.env | cut -d= -f2) /tmp/run.log
rm /tmp/run.log
```

Ожидаем нули, кроме IP из служебных строк. Каждое совпадение разобрать, итог записать сюда.
