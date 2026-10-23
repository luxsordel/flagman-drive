# @flagman/mobile

Клиентское приложение FLAGMAN Drive: iOS, Android и веб из одного кода (Expo SDK 58, Expo Router).

## Запуск

Зависимости ставятся из корня репозитория: `npm install`. Дальше — из корня:

```bash
npm run start --workspace @flagman/mobile
```

В терминале появится QR-код: откройте его в Expo Go (Android — приложением Expo Go, iOS — камерой).
Клавиша `w` открывает веб-версию в браузере. Только веб:

```bash
npm run web --workspace @flagman/mobile
```

Телефон и компьютер должны быть в одной сети. Если сеть мешает (VPN, корпоративный Wi-Fi), запустите
`npx expo start --tunnel` из `apps/mobile`.

## Вход и адрес API

Вход по номеру и коду из SMS работает с настоящим API (`apps/api`), поэтому сначала запустите его
вместе с Postgres и Redis — см. [docs/06-onboarding.md](../../docs/06-onboarding.md). Код из SMS
в пилоте пишется в лог API (`SMS_BACKEND=log`).

Адрес API задаёт `EXPO_PUBLIC_API_URL`, по умолчанию `http://localhost:8000`. Чтобы поменять,
скопируйте `apps/mobile/.env.example` в `apps/mobile/.env.local`: Expo читает его из папки приложения,
а не из корня репозитория. Значение попадает в бандл, поэтому секретов там быть не может.

- **Веб.** В корневом `.env` для API нужно `API_CORS_ORIGINS=http://localhost:8081`. Открывайте
  приложение и API оба через `localhost`: refresh-токен приходит в cookie с `SameSite=Strict`, а
  `127.0.0.1` и `localhost` браузер считает разными сайтами — cookie не дойдёт, и вход не сохранится.
- **iOS-симулятор** — адрес по умолчанию подходит. **Android-эмулятор** — `http://10.0.2.2:8000`.
- **Телефон с Expo Go.** API в docker compose слушает только `127.0.0.1`, с телефона он недоступен.
  Пока проверяйте вход в вебе или в эмуляторе.

Токены: в iOS и Android refresh-токен хранится в `expo-secure-store` (Keychain / Keystore), в вебе —
в HttpOnly cookie, access-токен везде только в памяти. Правила — `docs/03-security.md` и
[ADR-0009](../../docs/adr/0009-client-auth-jwt-and-sessions.md).

## Проверки

```bash
npm run lint --workspace @flagman/mobile
```

```bash
npm run typecheck --workspace @flagman/mobile
```

```bash
npm test --workspace @flagman/mobile
```

Перед PR, если менялись зависимости, — то же, что делает CI:

```bash
npm run check:deps --workspace @flagman/mobile
```

```bash
npm run check:bundle --workspace @flagman/mobile
```

## Структура

| Путь                | Что там                                                                      |
| ------------------- | ---------------------------------------------------------------------------- |
| `src/app/`          | Маршруты Expo Router (файл = экран). Только связывают экраны из `features/`  |
| `src/features/<x>/` | Экраны и логика по областям: `auth`, `home`, `kyc`, позже `map`, `rental`    |
| `src/api/`          | Общее для запросов к API: разбор ошибок Problem Details (ADR-0006)           |
| `src/ui/`           | Базовые компоненты (`AppText`, `Button`, `Card`…) — только на дизайн-токенах |
| `src/theme/`        | Тема по системной настройке (`useTheme`), шрифты Oswald и Open Sans          |
| `src/i18n/`         | i18next: `locales/ru.ts` — эталон, `en.ts` обязан иметь те же ключи          |

## Правила

- Строки UI — только через `t('…')`. Новый ключ добавляется в `ru.ts` и `en.ts`; ключи типизированы,
  пропущенный перевод не пройдёт typecheck.
- Цвета, шрифты, отступы и радиусы — из `@flagman/design-tokens` через `useTheme()` и `src/theme/fonts.ts`.
  У кастомных шрифтов каждое начертание — отдельное семейство (`headingFont(600)`, `bodyFont(400)`):
  Android игнорирует `fontWeight` для подключённых шрифтов.
- Зависимости Expo ставятся только `npx expo install <пакет>` из `apps/mobile`, а обновление SDK —
  отдельным PR. Почему и как — [ADR-0008](../../docs/adr/0008-expo-sdk-dependency-policy.md).
- Карты (MapLibre) не работают в Expo Go — для них понадобится dev build (ADR-0001).
- Фото документов (`src/features/kyc/photos.ts`) перекодируются в JPEG и после отправки или замены сразу
  удаляются из кэша приложения (`discardPhoto`); камера не сохраняет снимок в галерею. Не храните фото
  и ссылки на них ни в состоянии дольше экрана, ни в логах.
