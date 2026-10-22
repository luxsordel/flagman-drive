import { themes, type Palette, type ThemeName } from '@flagman/design-tokens';
import { DarkTheme, DefaultTheme, type Theme as NavigationTheme } from 'expo-router';
import { useColorScheme } from 'react-native';

export interface Theme {
  name: ThemeName;
  colors: Palette;
}

// Unknown or unsupported schemes fall back to light, the brand's primary look.
export function themeFor(scheme: string | null | undefined): Theme {
  const name: ThemeName = scheme === 'dark' ? 'dark' : 'light';
  return { name, colors: themes[name] };
}

export function useTheme(): Theme {
  return themeFor(useColorScheme());
}

/** Header and screen backgrounds drawn by the navigator itself. */
export function navigationTheme({ name, colors }: Theme): NavigationTheme {
  const base = name === 'dark' ? DarkTheme : DefaultTheme;
  return {
    ...base,
    colors: {
      primary: colors.primary,
      background: colors.background,
      card: colors.surface,
      text: colors.ink,
      border: colors.border,
      notification: colors.danger,
    },
  };
}
