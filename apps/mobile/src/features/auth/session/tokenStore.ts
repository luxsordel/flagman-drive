import { randomUUID } from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

import type { TokenStore } from './authSession';

const refreshTokenKey = 'flagman.refreshToken';
const deviceIdKey = 'flagman.deviceId';

// Keychain/Keystore only (docs/03-security.md). THIS_DEVICE_ONLY keeps the token out of iCloud
// Keychain sync and backups: a session must not move to another phone.
const options: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

/** iOS and Android: the app holds the refresh token itself and sends it in the request body. */
export function createTokenStore(): TokenStore {
  let deviceId: Promise<string> | null = null;
  return {
    transport: 'body',
    getRefreshToken: () => SecureStore.getItemAsync(refreshTokenKey, options),
    setRefreshToken: (token) =>
      token === null
        ? SecureStore.deleteItemAsync(refreshTokenKey, options)
        : SecureStore.setItemAsync(refreshTokenKey, token, options),
    getDeviceId: () =>
      (deviceId ??= readOrCreateDeviceId().catch((error: unknown) => {
        // A failed read must not stick: the next sign-in attempt tries the keychain again.
        deviceId = null;
        throw error;
      })),
  };
}

async function readOrCreateDeviceId(): Promise<string> {
  const stored = await SecureStore.getItemAsync(deviceIdKey, options);
  if (stored !== null) return stored;
  const id = randomUUID();
  await SecureStore.setItemAsync(deviceIdKey, id, options);
  return id;
}
