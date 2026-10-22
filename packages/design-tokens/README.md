# @flagman/design-tokens

Дизайн-токены FLAGMAN Drive: цвета (светлая и тёмная темы), типографика, отступы, радиусы.
Значения — из [docs/07-design-system.md](../../docs/07-design-system.md); при расхождении правятся код и документ одним PR.
Цвета, шрифты и отступы в приложениях берутся только отсюда, без хардкода.

Пакет отдаёт TypeScript-исходники (без сборки), без зависимостей в рантайме.

## Подключение

Пакет входит в npm workspaces из корня репозитория. В `package.json` приложения (`apps/mobile`, `apps/admin`):

```json
{ "dependencies": { "@flagman/design-tokens": "*" } }
```

Затем из корня репозитория: `npm install`.

## Мобильное приложение (Expo)

Expo сам находит workspaces, отдельная настройка Metro не нужна.

```tsx
import { useColorScheme, Text, View } from 'react-native';
import { minTapTarget, radius, spacing, tabularNumbers, themes } from '@flagman/design-tokens';

export function Price({ kopecks }: { kopecks: number }) {
  const colors = themes[useColorScheme() === 'dark' ? 'dark' : 'light'];
  return (
    <View
      style={{
        backgroundColor: colors.surface,
        borderColor: colors.border,
        borderWidth: 1,
        borderRadius: radius.card,
        padding: spacing.lg,
        minHeight: minTapTarget,
      }}
    >
      <Text style={{ color: colors.ink, fontVariant: tabularNumbers.reactNative }}>
        {(kopecks / 100).toFixed(2)} BYN
      </Text>
    </View>
  );
}
```

## Админка (Vite)

Токены попадают в CSS как переменные; тёмная тема переключается атрибутом `data-theme="dark"` на `<html>`.

```ts
// src/theme.ts
import { cssStaticVariables, cssVariables, themes } from '@flagman/design-tokens';

const block = (selector: string, variables: Record<string, string>): string =>
  `${selector} { ${Object.entries(variables)
    .map(([name, value]) => `${name}: ${value};`)
    .join(' ')} }`;

export function installTheme(): void {
  const style = document.createElement('style');
  style.textContent = [
    block(':root', { ...cssStaticVariables(), ...cssVariables(themes.light) }),
    block(':root[data-theme="dark"]', cssVariables(themes.dark)),
  ].join('\n');
  document.head.append(style);
}
```

```css
.card {
  background: var(--color-surface);
  border-radius: var(--radius-card);
  padding: var(--space-lg);
  font-family: var(--font-body);
  font-variant-numeric: tabular-nums;
}
```

## Что внутри

| Экспорт                                                  | Содержимое                                                                                                              |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `themes`, `lightPalette`, `darkPalette`                  | цвета: `primary`, `primaryStrong`, `ink`, `inkMuted`, `surface`, `background`, `border`, `success`, `warning`, `danger` |
| `fontFamily`, `fontWeight`, `fontSize`, `tabularNumbers` | Oswald (заголовки), Open Sans (текст), шкала 12–32                                                                      |
| `spacing`, `radius`, `minTapTarget`                      | сетка 4–32, радиусы 8/12/16/24, зона нажатия 44                                                                         |
| `cssVariables(palette)`, `cssStaticVariables()`          | CSS-переменные `--color-*`, `--space-*`, `--radius-*`, `--font-*`, `--tap-target`                                       |

Шрифты Oswald и Open Sans пакет не подключает — их грузит приложение (в Expo — `expo-font`, в админке — `@fontsource`).

## Разработка

```bash
npm run lint --workspace @flagman/design-tokens
```

```bash
npm test --workspace @flagman/design-tokens
```
