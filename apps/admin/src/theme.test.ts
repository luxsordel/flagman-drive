import { themes } from '@flagman/design-tokens';
import { afterEach, describe, expect, it } from 'vitest';

import { applyTheme, installTheme } from './theme';

const sheetText = (sheet: CSSStyleSheet): string =>
  Array.from(sheet.cssRules, (rule) => rule.cssText).join('\n');

let restoreAdoptedStyleSheets: (() => void) | undefined;

// jsdom, like current browsers, has adoptedStyleSheets; hiding it is the only way to reach the
// <style> fallback that older browsers still take.
function hideAdoptedStyleSheets(): void {
  let owner: object | null = document;
  while (owner && !Object.hasOwn(owner, 'adoptedStyleSheets'))
    owner = Object.getPrototypeOf(owner) as object | null;
  if (!owner) return;
  const target = owner;
  const descriptor = Object.getOwnPropertyDescriptor(target, 'adoptedStyleSheets');
  Reflect.deleteProperty(target, 'adoptedStyleSheets');
  restoreAdoptedStyleSheets = () => {
    if (descriptor) Object.defineProperty(target, 'adoptedStyleSheets', descriptor);
  };
}

describe('theme', () => {
  afterEach(() => {
    restoreAdoptedStyleSheets?.();
    restoreAdoptedStyleSheets = undefined;
    document.adoptedStyleSheets = [];
    document.head.querySelectorAll('style[data-flagman-theme]').forEach((style) => {
      style.remove();
    });
  });

  it('exposes the design tokens as CSS variables for both themes', () => {
    hideAdoptedStyleSheets();
    installTheme();

    const css = document.head.querySelector('style[data-flagman-theme]')?.textContent ?? '';
    expect(css).toContain(`--color-primary: ${themes.light.primary}`);
    expect(css).toContain(`--color-primary: ${themes.dark.primary}`);
    expect(css).toContain('--space-lg:');
  });

  it('installs the variables only once', () => {
    hideAdoptedStyleSheets();
    installTheme();
    installTheme();

    expect(document.head.querySelectorAll('style[data-flagman-theme]')).toHaveLength(1);
  });

  it('uses a constructed sheet, which a strict CSP allows, where the browser has one', () => {
    installTheme();
    installTheme();

    expect(document.adoptedStyleSheets).toHaveLength(1);
    expect(document.head.querySelector('style[data-flagman-theme]')).toBeNull();
    const [sheet] = document.adoptedStyleSheets;
    expect(sheet && sheetText(sheet)).toContain('--color-primary');
  });

  it('marks the document with the chosen theme', () => {
    applyTheme('dark');

    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
