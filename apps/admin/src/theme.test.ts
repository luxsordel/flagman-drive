import { themes } from '@flagman/design-tokens';
import { afterEach, describe, expect, it } from 'vitest';

import { applyTheme, installTheme } from './theme';

const sheetText = (sheet: CSSStyleSheet): string =>
  Array.from(sheet.cssRules, (rule) => rule.cssText).join('\n');

describe('theme', () => {
  afterEach(() => {
    Reflect.deleteProperty(document, 'adoptedStyleSheets');
  });

  it('exposes the design tokens as CSS variables for both themes', () => {
    installTheme();

    const css = document.head.querySelector('style[data-flagman-theme]')?.textContent ?? '';
    expect(css).toContain(`--color-primary: ${themes.light.primary}`);
    expect(css).toContain(`--color-primary: ${themes.dark.primary}`);
    expect(css).toContain('--space-lg:');
  });

  it('installs the variables only once', () => {
    installTheme();
    installTheme();

    expect(document.head.querySelectorAll('style[data-flagman-theme]')).toHaveLength(1);
  });

  it('uses a constructed sheet, which a strict CSP allows, where the browser has one', () => {
    // jsdom has no adoptedStyleSheets; browsers do.
    Object.defineProperty(document, 'adoptedStyleSheets', {
      configurable: true,
      writable: true,
      value: [],
    });

    installTheme();
    installTheme();

    expect(document.adoptedStyleSheets).toHaveLength(1);
    const [sheet] = document.adoptedStyleSheets;
    expect(sheet && sheetText(sheet)).toContain('--color-primary');
  });

  it('marks the document with the chosen theme', () => {
    applyTheme('dark');

    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
