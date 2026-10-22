import { afterEach, describe, expect, it, vi } from 'vitest';

import { fakeStaffAuthApi, testStaff } from '../test/auth';
import { createStaffSession } from './session';

const fresh = (accessToken: string) => ({ accessToken, expiresInSeconds: 900 });

describe('staff session', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('restores the staff member from the refresh cookie and keeps the token in memory', async () => {
    const api = fakeStaffAuthApi(testStaff('operator'));
    const session = createStaffSession(api);

    expect(await session.restore()).toEqual(testStaff('operator'));
    expect(session.accessToken()).toBe('access-1');
    expect(api.me).toHaveBeenCalledWith('access-1');
  });

  it('returns null without a live session', async () => {
    const session = createStaffSession(fakeStaffAuthApi(null));

    expect(await session.restore()).toBeNull();
    expect(session.accessToken()).toBeNull();
  });

  it('sends one refresh for concurrent callers', async () => {
    const api = fakeStaffAuthApi(testStaff());
    const session = createStaffSession(api);

    await Promise.all([session.restore(), session.restore()]);

    expect(api.refresh).toHaveBeenCalledTimes(1);
  });

  it('retries a rejected refresh once, as another tab may have just rotated the cookie', async () => {
    const api = fakeStaffAuthApi(testStaff());
    api.refresh.mockResolvedValueOnce('rejected');
    const session = createStaffSession(api);

    expect(await session.restore()).toEqual(testStaff());
    expect(api.refresh).toHaveBeenCalledTimes(2);
  });

  it('gives up after the second rejection', async () => {
    const api = fakeStaffAuthApi(testStaff());
    api.refresh.mockResolvedValue('rejected');
    const session = createStaffSession(api);

    expect(await session.restore()).toBeNull();
    expect(api.refresh).toHaveBeenCalledTimes(2);
  });

  it('forgets the token when the staff profile cannot be loaded', async () => {
    const api = fakeStaffAuthApi(testStaff());
    api.me.mockRejectedValue(new Error('network'));
    const session = createStaffSession(api);

    await expect(session.restore()).rejects.toThrow('network');
    expect(session.accessToken()).toBeNull();
  });

  it('refreshes a minute before the access token expires', async () => {
    vi.useFakeTimers();
    const api = fakeStaffAuthApi(testStaff());
    api.refresh.mockResolvedValueOnce(fresh('access-1')).mockResolvedValueOnce(fresh('access-2'));
    const session = createStaffSession(api);
    await session.restore();

    await vi.advanceTimersByTimeAsync(839_000);
    expect(session.accessToken()).toBe('access-1');

    await vi.advanceTimersByTimeAsync(1_000);
    expect(session.accessToken()).toBe('access-2');
  });

  it('retries a background refresh that failed on the network', async () => {
    vi.useFakeTimers();
    const api = fakeStaffAuthApi(testStaff());
    api.refresh
      .mockResolvedValueOnce(fresh('access-1'))
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(fresh('access-2'));
    const session = createStaffSession(api);
    await session.restore();

    await vi.advanceTimersByTimeAsync(840_000);
    expect(session.accessToken()).toBe('access-1');

    await vi.advanceTimersByTimeAsync(15_000);
    expect(session.accessToken()).toBe('access-2');
  });

  it('ends the session when a background refresh is rejected', async () => {
    vi.useFakeTimers();
    const api = fakeStaffAuthApi(testStaff());
    api.refresh.mockResolvedValueOnce(fresh('access-1')).mockResolvedValue('rejected');
    const session = createStaffSession(api);
    const signedOut = vi.fn();
    session.onSignedOut(signedOut);
    await session.restore();

    await vi.advanceTimersByTimeAsync(840_000);

    expect(signedOut).toHaveBeenCalledOnce();
    expect(session.accessToken()).toBeNull();
  });

  it('signs out on the server and locally', async () => {
    const api = fakeStaffAuthApi(testStaff());
    const session = createStaffSession(api);
    const signedOut = vi.fn();
    session.onSignedOut(signedOut);
    await session.restore();

    await session.signOut();

    expect(api.logout).toHaveBeenCalledOnce();
    expect(signedOut).toHaveBeenCalledOnce();
    expect(session.accessToken()).toBeNull();
  });

  it('forgets the token even when the server sign-out fails', async () => {
    const api = fakeStaffAuthApi(testStaff());
    api.logout.mockRejectedValue(new Error('network'));
    const session = createStaffSession(api);
    const signedOut = vi.fn();
    session.onSignedOut(signedOut);
    await session.restore();

    await expect(session.signOut()).rejects.toThrow('network');

    expect(signedOut).toHaveBeenCalledOnce();
    expect(session.accessToken()).toBeNull();
  });

  it('keeps no token from a background refresh that lands during sign-out', async () => {
    vi.useFakeTimers();
    const api = fakeStaffAuthApi(testStaff());
    let land: (token: string) => void = () => undefined;
    api.refresh.mockResolvedValueOnce(fresh('access-1')).mockReturnValueOnce(
      new Promise((resolve) => {
        land = (token) => {
          resolve(fresh(token));
        };
      }),
    );
    const session = createStaffSession(api);
    await session.restore();
    await vi.advanceTimersByTimeAsync(840_000);

    const signingOut = session.signOut();
    land('access-2');
    await signingOut;

    expect(session.accessToken()).toBeNull();
    await vi.advanceTimersByTimeAsync(900_000);
    expect(api.refresh).toHaveBeenCalledTimes(2);
  });
});
