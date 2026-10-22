import { randomUUID } from 'expo-crypto';

import type { TokenStore } from './authSession';

const deviceIdKey = 'flagman.deviceId';
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Browser: there is no secure storage, so the refresh token lives in an HttpOnly cookie that
 * scripts cannot read (docs/03-security.md), and the access token only in memory.
 */
export function createTokenStore(): TokenStore {
  let deviceId: string | null = null;
  return {
    transport: 'cookie',
    getRefreshToken: () => Promise.resolve(null),
    setRefreshToken: () => Promise.resolve(),
    getDeviceId: () => Promise.resolve((deviceId ??= readOrCreateDeviceId())),
  };
}

// The id is a label for this browser's sessions, not a secret, so localStorage is enough.
// Storage can be blocked (private mode, site data off): then every page load is a new device.
function readOrCreateDeviceId(): string {
  try {
    const stored = localStorage.getItem(deviceIdKey);
    if (stored !== null && uuidPattern.test(stored)) return stored;
  } catch {
    // Falls through to a fresh id.
  }
  const id = randomUUID();
  try {
    localStorage.setItem(deviceIdKey, id);
  } catch {
    // Kept in memory for this page load only.
  }
  return id;
}
