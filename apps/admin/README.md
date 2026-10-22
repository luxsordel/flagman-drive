# apps/admin — админ-панель FLAGMAN Drive

Веб-панель для операторов поддержки, техников и администраторов (роли — [docs/01-vision-and-mvp.md](../../docs/01-vision-and-mvp.md)).
React + Vite + TypeScript strict, роутинг — `react-router`, переводы — `i18next` (ru, en).

Сейчас это каркас: макет с боковым меню и шапкой, заглушки разделов, сессия сотрудника и меню по ролям.
Форма входа подключается к API вместе с эндпоинтами сотрудников (#32, #39); до этого войти нельзя,
и панель всегда открывается на странице входа. Разделы наполняются в следующих задачах.

## Запуск

Все команды — из корня репозитория, работают в PowerShell и Git Bash.

```bash
npm install
```

```bash
npm run dev --workspace @flagman/admin
```

Панель открывается на http://127.0.0.1:5173 (только localhost, как и остальной локальный стек).
Запросы к `/staff/*` и `/admin/*` Vite проксирует на API (http://127.0.0.1:8000 из `infra/docker-compose.yml`),
чтобы у панели и API был один origin: не нужен CORS с передачей cookie, а refresh-cookie сотрудника
ставится на адрес самой панели.

## Структура

```
src/
  main.tsx        # точка входа: шрифты, тема, сессия, роутер
  routes.tsx      # маршруты: /login и разделы внутри макета, закрытые входом и ролью
  navigation.ts   # разделы бокового меню и роли, которым они видны
  auth/           # сессия сотрудника (access-токен в памяти, обновление по cookie), охрана маршрутов
  theme.ts        # дизайн-токены → CSS-переменные, светлая и тёмная темы
  styles.css      # стили только через var(--color-*), var(--space-*), var(--radius-*), var(--font-*)
  i18n/           # ru.json, en.json; ключи типизированы
  layout/         # макет, шапка
  pages/          # вход, заглушка раздела, «нет доступа», 404
  test/           # рендер приложения и подмена API входа для тестов
```

## Правила

- Все строки интерфейса — только через `t('...')`, перевод добавляется сразу в `ru.json` и `en.json`
  (тест проверяет, что ключи совпадают).
- Цвета, шрифты, отступы и радиусы — только из `@flagman/design-tokens` через CSS-переменные.
- Токены доступа в вебе хранятся только в памяти, refresh-токен — в `HttpOnly` cookie
  ([docs/03-security.md](../../docs/03-security.md)); не в `localStorage` и не в `sessionStorage`.

## Проверки

```bash
npm run lint --workspace @flagman/admin
```

```bash
npm run typecheck --workspace @flagman/admin
```

```bash
npm test --workspace @flagman/admin
```

```bash
npm run build --workspace @flagman/admin
```
