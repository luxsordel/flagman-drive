import { fontWeight, themes } from '@flagman/design-tokens';

import { splashBackground } from '../../app.config';
import { bodyFont, fontAssets, headingFont } from './fonts';
import { navigationTheme, themeFor } from './theme';

describe('themeFor', () => {
  it('follows the system scheme', () => {
    expect(themeFor('dark').colors).toBe(themes.dark);
    expect(themeFor('light').colors).toBe(themes.light);
  });

  it('falls back to light when the scheme is unknown', () => {
    expect(themeFor(null).name).toBe('light');
    expect(themeFor('unspecified').name).toBe('light');
  });

  it('paints navigator backgrounds from tokens', () => {
    expect(navigationTheme(themeFor('dark')).colors.background).toBe(themes.dark.background);
  });
});

describe('fonts', () => {
  it('loads exactly the weights the design tokens allow', () => {
    const expected = [...fontWeight.heading.map(headingFont), ...fontWeight.body.map(bodyFont)];
    expect(Object.keys(fontAssets).sort()).toEqual(expected.sort());
  });
});

describe('app.config', () => {
  it('splash background matches the token background in both themes', () => {
    expect(splashBackground).toEqual({
      light: themes.light.background,
      dark: themes.dark.background,
    });
  });
});
