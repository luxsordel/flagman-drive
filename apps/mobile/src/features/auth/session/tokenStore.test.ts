import * as SecureStore from 'expo-secure-store';

import { createTokenStore as createNativeStore } from './tokenStore';
import { createTokenStore as createWebStore } from './tokenStore.web';

const mockKeychain = new Map<string, string>();

jest.mock('expo-crypto', () => ({ randomUUID: () => '11111111-2222-4333-8444-555555555555' }));
jest.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'device-only',
  getItemAsync: jest.fn((key: string) => Promise.resolve(mockKeychain.get(key) ?? null)),
  setItemAsync: jest.fn((key: string, value: string) => {
    mockKeychain.set(key, value);
    return Promise.resolve();
  }),
  deleteItemAsync: jest.fn((key: string) => {
    mockKeychain.delete(key);
    return Promise.resolve();
  }),
}));

beforeEach(() => {
  mockKeychain.clear();
  jest.clearAllMocks();
});

describe('native token store', () => {
  it('keeps the refresh token in the keychain, bound to this device', async () => {
    const store = createNativeStore();

    await store.setRefreshToken('refresh-1');

    expect(await store.getRefreshToken()).toBe('refresh-1');
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('flagman.refreshToken', 'refresh-1', {
      keychainAccessible: 'device-only',
    });
  });

  it('deletes the refresh token on sign-out', async () => {
    const store = createNativeStore();
    await store.setRefreshToken('refresh-1');

    await store.setRefreshToken(null);

    expect(await store.getRefreshToken()).toBeNull();
  });

  it('creates the device id once and keeps it across launches', async () => {
    const first = await createNativeStore().getDeviceId();
    const nextLaunch = await createNativeStore().getDeviceId();

    expect(first).toBe('11111111-2222-4333-8444-555555555555');
    expect(nextLaunch).toBe(first);
    expect(SecureStore.setItemAsync).toHaveBeenCalledTimes(1);
  });
});

describe('web token store', () => {
  const storage = new Map<string, string>();

  beforeAll(() => {
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
      },
    });
  });

  afterAll(() => {
    Reflect.deleteProperty(globalThis, 'localStorage');
  });

  beforeEach(() => {
    storage.clear();
  });

  it('never holds the refresh token, which stays in the HttpOnly cookie', async () => {
    const store = createWebStore();

    await store.setRefreshToken('refresh-1');

    expect(store.transport).toBe('cookie');
    expect(await store.getRefreshToken()).toBeNull();
    expect([...storage.values()]).not.toContain('refresh-1');
  });

  it('keeps the device id in localStorage', async () => {
    storage.set('flagman.deviceId', '7b6f0a54-3c1e-4d2a-9f5e-2b8c1d0e4f6a');

    expect(await createWebStore().getDeviceId()).toBe('7b6f0a54-3c1e-4d2a-9f5e-2b8c1d0e4f6a');
  });

  it('replaces a damaged device id instead of sending it to the API', async () => {
    storage.set('flagman.deviceId', 'not-a-uuid');

    expect(await createWebStore().getDeviceId()).toBe('11111111-2222-4333-8444-555555555555');
    expect(storage.get('flagman.deviceId')).toBe('11111111-2222-4333-8444-555555555555');
  });
});
