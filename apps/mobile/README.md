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

| Путь                | Что там                                                                          |
| ------------------- | -------------------------------------------------------------------------------- |
| `src/app/`          | Маршруты Expo Router (файл = экран). Только связывают экраны из `features/`      |
| `src/features/<x>/` | Экраны и логика по областям: `auth`, позже `map`, `rental`, `documents`          |
| `src/ui/`           | Базовые компоненты (`AppText`, `Button`, `TextField`) — только на дизайн-токенах |
| `src/theme/`        | Тема по системной настройке (`useTheme`), шрифты Oswald и Open Sans              |
| `src/i18n/`         | i18next: `locales/ru.ts` — эталон, `en.ts` обязан иметь те же ключи              |

## Правила

- Строки UI — только через `t('…')`. Новый ключ добавляется в `ru.ts` и `en.ts`; ключи типизированы,
  пропущенный перевод не пройдёт typecheck.
- Цвета, шрифты, отступы и радиусы — из `@flagman/design-tokens` через `useTheme()` и `src/theme/fonts.ts`.
  У кастомных шрифтов каждое начертание — отдельное семейство (`headingFont(600)`, `bodyFont(400)`):
  Android игнорирует `fontWeight` для подключённых шрифтов.
- Зависимости Expo ставятся только `npx expo install <пакет>` из `apps/mobile`, а обновление SDK —
  отдельным PR. Почему и как — [ADR-0008](../../docs/adr/0008-expo-sdk-dependency-policy.md).
- Карты (MapLibre) не работают в Expo Go — для них понадобится dev build (ADR-0001).
