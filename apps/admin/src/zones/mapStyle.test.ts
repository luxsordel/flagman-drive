import { themes } from '@flagman/design-tokens';
import { describe, expect, it } from 'vitest';

import { hexColor, zoneColor } from './mapStyle';

describe('zone colors', () => {
  it('takes the kind colors from the design tokens of each theme', () => {
    for (const palette of [themes.light, themes.dark]) {
      expect(zoneColor('allowed', palette)).toBe(palette.success);
      expect(zoneColor('forbidden', palette)).toBe(palette.danger);
      // Terra Draw throws on anything but #rrggbb; every token it gets must pass.
      expect(hexColor(palette.success)).toBe(palette.success);
      expect(hexColor(palette.danger)).toBe(palette.danger);
    }
  });

  it('refuses a color Terra Draw cannot take', () => {
    expect(() => hexColor('rgb(0, 0, 0)')).toThrow();
  });
});
