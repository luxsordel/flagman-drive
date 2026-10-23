import type { ExpoConfig } from 'expo/config';

// Native config is read before the JS bundle exists, so token values are repeated here;
// src/theme/theme.test.ts fails if they drift from @flagman/design-tokens.
export const splashBackground = { light: '#F4F7FB', dark: '#0B1424' } as const;

// The iOS permission prompt is drawn by the system before any JS runs, so it cannot use i18n.
const cameraPermission = {
  en: 'FLAGMAN Drive uses the camera to photograph your driving licence and passport for verification.',
  ru: 'FLAGMAN Drive использует камеру, чтобы сфотографировать права и паспорт для проверки.',
} as const;

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
  locales: {
    ru: { ios: { NSCameraUsageDescription: cameraPermission.ru } },
  },
  plugins: [
    'expo-router',
    'expo-localization',
    'expo-secure-store',
    [
      'expo-image-picker',
      {
        cameraPermission: cameraPermission.en,
        // The picker opens the system photo picker, which needs no library permission.
        photosPermission: false,
        microphonePermission: false,
      },
    ],
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
