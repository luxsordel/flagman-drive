import { cssStaticVariables, cssVariables, themes, type ThemeName } from '@flagman/design-tokens';

const block = (selector: string, variables: Record<string, string>): string =>
  `${selector} { ${Object.entries(variables)
    .map(([name, value]) => `${name}: ${value};`)
    .join(' ')} }`;

/** Puts the design tokens into CSS variables; the dark palette applies under `data-theme="dark"`. */
export function installTheme(): void {
  if (document.head.querySelector('style[data-flagman-theme]')) return;
  const style = document.createElement('style');
  style.dataset.flagmanTheme = '';
  style.textContent = [
    block(':root', { ...cssStaticVariables(), ...cssVariables(themes.light) }),
    block(':root[data-theme="dark"]', cssVariables(themes.dark)),
  ].join('\n');
  document.head.append(style);
}

export function preferredTheme(): ThemeName {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function currentTheme(): ThemeName {
  const theme = document.documentElement.dataset.theme;
  return theme === 'dark' || theme === 'light' ? theme : preferredTheme();
}

export function applyTheme(theme: ThemeName): void {
  document.documentElement.dataset.theme = theme;
}
