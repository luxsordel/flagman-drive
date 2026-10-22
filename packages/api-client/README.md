# @flagman/api-client

Типизированный клиент API, **сгенерированный** из OpenAPI бэкенда. Файлы `openapi.json` и `src/generated/`
руками не правятся — только перегенерируются. Обёртка `src/index.ts` — единственный написанный вручную код.

Формат ошибок — Problem Details ([ADR-0006](../../docs/adr/0006-api-error-format.md)), тип `Problem`.

## Перегенерация

Нужны Node и `uv`. Из корня репозитория:

```bash
npm run gen:api
```

Команда собирает схему из `apps/api` (без Postgres и Redis), обновляет `openapi.json` и `src/generated/schema.ts`.
Менялся API — запускайте её и коммитьте результат вместе с изменением. CI падает, если сгенерированное устарело.

## Подключение

Пакет входит в npm workspaces из корня репозитория. В `package.json` приложения (`apps/mobile`, `apps/admin`):

```json
{ "dependencies": { "@flagman/api-client": "*" } }
```

Затем из корня репозитория: `npm install`.

## Мобильное приложение (Expo)

Адрес API — публичная настройка клиента (не секрет): `EXPO_PUBLIC_API_URL`.
Токен хранится в `expo-secure-store`, не в `AsyncStorage`.

```ts
// src/api.ts
import { createApiClient } from '@flagman/api-client';
import * as SecureStore from 'expo-secure-store';

export const api = createApiClient({
  baseUrl: process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000',
  getAccessToken: () => SecureStore.getItemAsync('access_token'),
});
```

## Админка (Vite)

API вызывается с адреса самой админки: в разработке Vite проксирует `/staff` и `/admin` на API
(`apps/admin/vite.config.ts`): один origin с API, поэтому не нужен CORS с передачей cookie.
Access-токен хранится только в памяти сессии (`apps/admin/src/auth/session.ts`), не в `sessionStorage`
и не в `localStorage` ([docs/03-security.md](../../docs/03-security.md)).

```ts
// src/api.ts
import { createApiClient } from '@flagman/api-client';

export const api = createApiClient({
  baseUrl: window.location.origin,
  getAccessToken: () => session.accessToken(),
});
```

## Вызовы

Пути, параметры и ответы проверяются типами; ошибки приходят как `Problem`.

```ts
const { data, error } = await api.GET('/health');

if (error) {
  // error: Problem — title, status, detail?, request_id?, errors?
  console.warn(error.title, error.request_id);
} else {
  console.log(data.status); // 'ok' | 'fail'
}
```

Суммы приходят от сервера в копейках BYN (`int`); клиент их не пересчитывает и не отправляет свои.

## Разработка

```bash
npm test --workspace @flagman/api-client
```
