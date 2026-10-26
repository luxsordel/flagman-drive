import type { ExpoConfig } from 'expo/config';

// Native config is read before the JS bundle exists, so token values are repeated here;
// src/theme/theme.test.ts fails if they drift from @flagman/design-tokens.
export const splashBackground = { light: '#F4F7FB', dark: '#0B1424' } as const;

// The iOS permission prompt is drawn by the system before any JS runs, so it cannot use i18n.
const cameraPermission = {
  en: 'FLAGMAN Drive uses the camera to photograph your driving licence and passport for verification.',
  ru: 'FLAGMAN Drive использует камеру, чтобы сфотографировать права и паспорт для проверки.',
} as const;

// Asked only while the map is open: the location centres the map and shows the distance to cars.
const locationPermission = {
  en: 'FLAGMAN Drive uses your location to show the cars near you and how far they are.',
  ru: 'FLAGMAN Drive использует ваше местоположение, чтобы показать ближайшие машины и расстояние до них.',
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
    ru: {
      ios: {
        NSCameraUsageDescription: cameraPermission.ru,
        NSLocationWhenInUseUsageDescription: locationPermission.ru,
      },
    },
  },
  plugins: [
    'expo-router',
    'expo-dev-client',
    // The native map (ADR-0016); without it the dev build has no MapLibre SDK.
    '@maplibre/maplibre-react-native',
    [
      // Foreground only: the map never tracks the client in the background.
      'expo-location',
      {
        locationWhenInUsePermission: locationPermission.en,
        locationAlwaysAndWhenInUsePermission: false,
        locationAlwaysPermission: false,
        isIosBackgroundLocationEnabled: false,
        isAndroidBackgroundLocationEnabled: false,
        isAndroidForegroundServiceEnabled: false,
      },
    ],
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
      // The in-app camera for the selfie on Android, where the system camera may ignore
      // the request for the front one. Photos only: no microphone, no barcode scanner.
      'expo-camera',
      {
        cameraPermission: cameraPermission.en,
        microphonePermission: false,
        recordAudioAndroid: false,
        barcodeScannerEnabled: false,
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
