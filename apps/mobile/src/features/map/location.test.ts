import { fixTimeoutMs, locateOnce } from './location';

const mockLocation = { status: 'granted', position: null as unknown };
jest.mock('expo-location', () => ({
  Accuracy: { Balanced: 3, High: 4 },
  PermissionStatus: { GRANTED: 'granted' },
  requestForegroundPermissionsAsync: () => Promise.resolve({ status: mockLocation.status }),
  // Never settles when there is no position: a phone indoors waiting for a fix.
  getCurrentPositionAsync: () =>
    mockLocation.position === null
      ? new Promise(() => undefined)
      : Promise.resolve(mockLocation.position),
}));

describe('locateOnce', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockLocation.status = 'granted';
    mockLocation.position = null;
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('returns the precise fix', async () => {
    mockLocation.position = { coords: { latitude: 53.9, longitude: 27.56 } };

    await expect(locateOnce()).resolves.toEqual({
      kind: 'located',
      location: { lat: 53.9, lon: 27.56 },
    });
  });

  it('gives up on a fix that does not come', async () => {
    const fix = locateOnce();
    await jest.advanceTimersByTimeAsync(fixTimeoutMs);

    await expect(fix).resolves.toEqual({ kind: 'unavailable' });
  });

  it('says when the permission is refused', async () => {
    mockLocation.status = 'denied';

    await expect(locateOnce()).resolves.toEqual({ kind: 'denied' });
  });
});
