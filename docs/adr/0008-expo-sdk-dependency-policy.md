# ADR-0008. Версии зависимостей мобилки задаёт Expo SDK

- Статус: предложено
- Дата: 2026-10-22

## Контекст
Expo SDK — согласованный набор версий: `expo-*`, `react-native`, `react`, `react-native-*`, `jest-expo`,
`@types/react` и `typescript`. Если версии расходятся, проверки на компьютере проходят, а приложение
падает на телефоне (нативный модуль не той версии). Мешают три вещи, найденные на каркасе мобилки (#4):

- **npm workspaces поднимают в корень самую свежую версию.** Многие пакеты Expo объявляют
  `react: "*"`, поэтому в корень попадал `react@19.3.1`, а приложение оставалось на `19.3.0` из SDK.
  Две копии React ломают хуки, а `react-dom` при этом не совпадает с `react`.
- **Dependabot обновляет пакеты по одному** и не знает про наборы SDK.
- **В репозитории разные TypeScript:** SDK 58 рассчитан на `~6.0.3`, а пакеты `packages/*` — на `~7.0.2`.
  `typescript-eslint` поддерживает только `<6.1.0`.

## Решение
1. В `apps/mobile` версии Expo-пакетов ставятся только через `npx expo install`, без ручной правки
   диапазонов. Обновление SDK — отдельный PR: `npx expo install expo@^NN --fix`.
2. Корневой `package.json` закрепляет одну версию React на весь репозиторий через `overrides`
   (`react`, `react-dom`) — ту, что требует текущий SDK. Админка (`apps/admin`) использует ту же версию.
3. CI проверяет согласованность при каждом изменении JS-частей:
   - `npm run check:deps` → `expo install --check` (версии совпадают с SDK);
   - `npm run check:bundle` → `expo export --platform all` (бандл собирается для Android, iOS и веба);
   - тест `apps/mobile/src/singleReact.test.ts` падает, если появилась вторая копия React.
4. Dependabot собирает Expo-пакеты в одну группу `expo-sdk`; мажорные версии `expo`/`jest-expo` и любые
   обновления `react`, `react-dom`, `react-native` он не предлагает — они меняются только вместе с SDK.
5. TypeScript в `apps/mobile` — версия из SDK (сейчас `~6.0.3`). В `packages/*` остаётся своя версия.
6. Линт мобилки не использует `eslint-config-expo`: он тянет `eslint-plugin-react` и
   `eslint-plugin-import`, которые не поддерживают ESLint 10. Вместо него — `typescript-eslint`
   (как в `packages/*`), `eslint-plugin-react-hooks` и `eslint-plugin-expo`.

## Альтернативы
- **Отдельный lockfile для мобилки (без workspaces).** Убирает поднятие React в корень, но ломает импорт
  `@flagman/design-tokens` и `@flagman/api-client` без публикации пакетов и даёт второй `npm ci` в CI.
- **`overrides` только внутри `@flagman/mobile`.** Если админка поднимет в корень другой React, пакеты Expo
  из корня возьмут его, и копий снова станет две. Одна версия на репозиторий проще и надёжнее.
- **Обновлять Expo-пакеты Dependabot'ом по одному.** Каждый такой PR ломает `check:deps`; ревьюер
  тратит время на заведомо неверные PR.

## Последствия
- Обновление React в админке — только вместе с SDK мобилки (раз в несколько месяцев).
- При обновлении SDK нужно поменять `overrides` в корне на версию React из нового SDK; иначе `npm install`
  выдаст конфликт, а `singleReact.test.ts` или `check:deps` упадут — ошибку видно сразу.
- CI для JS-частей стал дольше примерно на минуту (сборка бандлов).
- Когда `typescript-eslint` начнёт поддерживать TypeScript 7, линт `packages/*` нужно перевести на ту же
  версию, что и typecheck: сейчас линт фактически идёт на TypeScript из корня `node_modules`.
