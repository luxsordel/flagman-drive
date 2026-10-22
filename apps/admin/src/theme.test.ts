import { themes } from '@flagman/design-tokens';
import { describe, expect, it } from 'vitest';

import { applyTheme, installTheme } from './theme';

describe('theme', () => {
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

  it('marks the document with the chosen theme', () => {
    applyTheme('dark');

    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
