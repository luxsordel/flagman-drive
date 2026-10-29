import { cssStaticVariables, cssVariables, themes, type ThemeName } from '@flagman/design-tokens';

const block = (selector: string, variables: Record<string, string>): string =>
  `${selector} { ${Object.entries(variables)
    .map(([name, value]) => `${name}: ${value};`)
    .join(' ')} }`;

const themeCss = (): string =>
  [
    block(':root', { ...cssStaticVariables(), ...cssVariables(themes.light) }),
    block(':root[data-theme="dark"]', cssVariables(themes.dark)),
  ].join('\n');

let installedSheet: CSSStyleSheet | undefined;

/** Puts the design tokens into CSS variables; the dark palette applies under `data-theme="dark"`. */
export function installTheme(): void {
  // A constructed sheet rather than a <style> element where the browser has one: the stand's CSP
  // (style-src 'self', infra/stand/web/Caddyfile) blocks inline style text, but not the CSSOM.
  if (Reflect.has(document, 'adoptedStyleSheets')) {
    if (installedSheet && document.adoptedStyleSheets.includes(installedSheet)) return;
    installedSheet = new CSSStyleSheet();
    installedSheet.replaceSync(themeCss());
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, installedSheet];
    return;
  }
  if (document.head.querySelector('style[data-flagman-theme]')) return;
  const style = document.createElement('style');
  style.dataset.flagmanTheme = '';
  style.textContent = themeCss();
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
