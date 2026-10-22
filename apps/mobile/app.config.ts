import type { ExpoConfig } from 'expo/config';

// Native config is read before the JS bundle exists, so token values are repeated here;
// src/theme/theme.test.ts fails if they drift from @flagman/design-tokens.
export const splashBackground = { light: '#F4F7FB', dark: '#0B1424' } as const;

const config: ExpoConfig = {
  name: 'FLAGMAN Drive',
  slug: 'flagman-drive',
  version: '0.1.0',
  scheme: 'flagmandrive',
  orientation: 'portrait',
  userInterfaceStyle: 'automatic',
  backgroundColor: splashBackground.light,
  ios: {
    bundleIdentifier: 'by.flagman.drive',
    supportsTablet: false,
  },
  android: {
    package: 'by.flagman.drive',
  },
  web: {
    output: 'single',
  },
  plugins: [
    'expo-router',
    'expo-localization',
    [
      'expo-splash-screen',
      {
        backgroundColor: splashBackground.light,
        dark: { backgroundColor: splashBackground.dark },
      },
    ],
  ],
};

export default config;
