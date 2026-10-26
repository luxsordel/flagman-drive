# @flagman/mobile

Клиентское приложение FLAGMAN Drive: iOS, Android и веб из одного кода (Expo SDK 58, Expo Router).

## Запуск

Зависимости ставятся из корня репозитория: `npm install`. Карта на iOS и Android — нативный модуль MapLibre,
в Expo Go его нет ([ADR-0016](../../docs/adr/0016-maplibre-react-native-dev-build.md)). Поэтому способов три.

### Веб — всё, включая карту

```bash
npm run web --workspace @flagman/mobile
```

### Expo Go — быстро, но без карты

```bash
npm run go --workspace @flagman/mobile
```

В терминале появится QR-код: откройте его в Expo Go (Android — приложением Expo Go, iOS — камерой).
Вместо карты будет заглушка, остальные экраны работают. Телефон и компьютер должны быть в одной сети;
если сеть мешает (VPN, корпоративный Wi-Fi), запустите `npx expo start --go --tunnel` из `apps/mobile`.

### Dev-сборка — с картой (Android Studio)

Один раз:

1. Поставьте Android Studio (`winget install Google.AndroidStudio`) и пройдите мастер первого запуска:
   он скачает Android SDK.
2. Пропишите переменные окружения (PowerShell), затем перезапустите терминал:

   ```powershell
   setx ANDROID_HOME "$env:LOCALAPPDATA\Android\Sdk"
   ```

   ```powershell
   setx JAVA_HOME "C:\Program Files\Android\Android Studio\jbr"
   ```

   И добавьте `%LOCALAPPDATA%\Android\Sdk\platform-tools` в `PATH` — там `adb`.

3. Создайте эмулятор в Android Studio (Device Manager) или включите на телефоне отладку по USB.

Каждый раз — запустите эмулятор или подключите телефон (`adb devices` должен его показать) и:

```bash
npm run android --workspace @flagman/mobile
```

Команда соберёт и поставит приложение и запустит Metro. Первая сборка — 10–15 минут, дальше изменения JS
приходят сразу. Пересобирать нужно только после смены нативных зависимостей или `app.config.ts`;
если приложение уже стоит, хватит `npm run start --workspace @flagman/mobile`. Папка `android/`
генерируется и в git не попадает. На macOS то же самое делает `npm run ios` через Xcode.

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
- **iOS-симулятор** — адрес по умолчанию подходит.
- **Android-эмулятор и телефон по USB** — выполните `adb reverse tcp:8000 tcp:8000`, и адрес по умолчанию
  заработает (повторять после перезапуска эмулятора или переподключения телефона).
- **Телефон с Expo Go по Wi-Fi.** API в docker compose слушает только `127.0.0.1`, с телефона он недоступен.
  Подключите телефон по USB и используйте `adb reverse`, или проверяйте вход в вебе.

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
| `src/features/<x>/` | Экраны и логика по областям: `auth`, `kyc`, `map`, `profile`, позже `rental` |
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
- Карта (`src/features/map/`): нативный модуль MapLibre подключается лениво и только вне Expo Go
  (`isExpoGo`), иначе Expo Go упадёт при старте. Не импортируйте `@maplibre/maplibre-react-native`
  напрямую из других мест. Координаты клиента не логируйте и не сохраняйте (`docs/03-security.md`).
- Фото документов (`src/features/kyc/photos.ts`) перекодируются в JPEG и после отправки или замены сразу
  удаляются из кэша приложения (`discardPhoto`); камера не сохраняет снимок в галерею. Не храните фото
  и ссылки на них ни в состоянии дольше экрана, ни в логах.
