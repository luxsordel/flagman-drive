import { deviceId, json, problem, testSession, tokens } from './testing';

const phone = '+375291234567';
const accepted = json({ expires_in: 300, resend_after: 60 }, 202);

describe('AuthSession sign-in', () => {
  it('remembers the phone and when a new code may be requested', async () => {
    const { api, session } = testSession({ now: () => 1_000 });
    api.on('/auth/otp/request', accepted);

    expect(await session.requestCode(phone)).toBeNull();

    expect(api.calls).toEqual([{ path: '/auth/otp/request', body: { phone } }]);
    expect(session.getPendingSignIn()).toEqual({ phone, resendAvailableAt: 61_000 });
  });

  it.each([
    ['otp-expired', 400, 'otpExpired'],
    ['sign-in-unavailable', 503, 'signInUnavailable'],
    ['sign-in-not-allowed', 403, 'signInNotAllowed'],
    ['something-new', 400, 'unknown'],
  ])('maps the %s problem to %s', async (name, status, kind) => {
    const { api, session } = testSession();
    api.on('/auth/otp/request', problem(name, status));

    expect(await session.requestCode(phone)).toEqual({ kind });
    expect(session.getPendingSignIn()).toBeNull();
  });

  it('passes Retry-After with a rate limit', async () => {
    const { api, session } = testSession();
    api.on('/auth/otp/request', problem('rate-limited', 429, { 'Retry-After': '42' }));

    expect(await session.requestCode(phone)).toEqual({ kind: 'rateLimited', retryAfter: 42 });
  });

  it('reports a network error when the API cannot be reached', async () => {
    const { api, session } = testSession();
    api.on('/auth/otp/request', new TypeError('Network request failed'));

    expect(await session.requestCode(phone)).toEqual({ kind: 'network' });
  });

  it('exchanges the code for tokens and keeps the refresh token in the store', async () => {
    const { api, session, store } = testSession();
    api.on('/auth/otp/request', accepted);
    api.on('/auth/otp/verify', json(tokens(1)));
    await session.requestCode(phone);

    expect(await session.verifyCode('123456')).toBeNull();

    expect(api.calls.at(-1)?.body).toEqual({
      phone,
      code: '123456',
      device_id: deviceId,
      refresh_token_transport: 'body',
    });
    expect(session.getStatus()).toBe('signedIn');
    expect(store.refreshToken).toBe('refresh-1');
    expect(await session.getAccessToken()).toBe('access-1');
  });

  it('asks the web API to put the refresh token in a cookie', async () => {
    const { api, session } = testSession({ transport: 'cookie' });
    api.on('/auth/otp/request', accepted);
    api.on('/auth/otp/verify', json(tokens(1, 'cookie')));
    await session.requestCode(phone);

    await session.verifyCode('123456');

    expect(api.calls.at(-1)?.body).toMatchObject({ refresh_token_transport: 'cookie' });
    expect(session.getStatus()).toBe('signedIn');
  });

  it('stays on the code step after a wrong code', async () => {
    const { api, session } = testSession();
    api.on('/auth/otp/request', accepted);
    api.on('/auth/otp/verify', problem('otp-invalid', 400));
    await session.requestCode(phone);

    expect(await session.verifyCode('000000')).toEqual({ kind: 'otpInvalid' });
    expect(session.getPendingSignIn()?.phone).toBe(phone);
    expect(session.getStatus()).not.toBe('signedIn');
  });
});

describe('AuthSession tokens', () => {
  async function signedIn(now: () => number, transport: 'body' | 'cookie' = 'body') {
    const setup = testSession({ now, transport });
    setup.api.on('/auth/otp/request', accepted);
    setup.api.on('/auth/otp/verify', json(tokens(1, transport)));
    await setup.session.requestCode(phone);
    await setup.session.verifyCode('123456');
    setup.api.calls.length = 0;
    return setup;
  }

  it('reuses the access token while it has more than a minute left', async () => {
    let clock = 0;
    const { api, session } = await signedIn(() => clock);
    clock = 839_000;

    expect(await session.getAccessToken()).toBe('access-1');
    expect(api.calls).toEqual([]);
  });

  it('rotates the refresh token shortly before the access token expires', async () => {
    let clock = 0;
    const { api, session, store } = await signedIn(() => clock);
    api.on('/auth/token/refresh', json(tokens(2)));
    clock = 841_000;

    expect(await session.getAccessToken()).toBe('access-2');
    expect(api.calls).toEqual([
      { path: '/auth/token/refresh', body: { refresh_token: 'refresh-1' } },
    ]);
    expect(store.refreshToken).toBe('refresh-2');
  });

  it('shares one refresh between parallel callers', async () => {
    let clock = 0;
    const { api, session } = await signedIn(() => clock);
    api.on('/auth/token/refresh', json(tokens(2)));
    clock = 900_000;

    const results = await Promise.all([session.getAccessToken(), session.getAccessToken()]);

    expect(results).toEqual(['access-2', 'access-2']);
    expect(api.calls).toHaveLength(1);
  });

  it('signs out on the app without a retry when the refresh token is rejected', async () => {
    let clock = 0;
    const { api, session, store } = await signedIn(() => clock);
    api.on('/auth/token/refresh', problem('refresh-token-invalid', 401));
    clock = 900_000;

    expect(await session.getAccessToken()).toBeNull();
    expect(api.calls).toHaveLength(1);
    expect(session.getStatus()).toBe('signedOut');
    expect(store.refreshToken).toBeNull();
  });

  it('retries once on the web, where another tab may have rotated the cookie', async () => {
    let clock = 0;
    const { api, session } = await signedIn(() => clock, 'cookie');
    api.on('/auth/token/refresh', problem('refresh-token-invalid', 401), json(tokens(2, 'cookie')));
    clock = 900_000;

    expect(await session.getAccessToken()).toBe('access-2');
    expect(api.calls).toEqual([
      { path: '/auth/token/refresh', body: {} },
      { path: '/auth/token/refresh', body: {} },
    ]);
  });

  it('keeps the session when the refresh fails for a reason other than 401', async () => {
    let clock = 0;
    const { api, session, store } = await signedIn(() => clock);
    api.on('/auth/token/refresh', problem('internal', 500));
    clock = 900_000;

    await expect(session.getAccessToken()).rejects.toThrow('HTTP 500');
    expect(session.getStatus()).toBe('signedIn');
    expect(store.refreshToken).toBe('refresh-1');
  });

  it('ends the server session on sign-out and forgets the tokens', async () => {
    const { api, session, store } = await signedIn(() => 0);
    api.on('/auth/logout', new Response(null, { status: 204 }));

    await session.signOut();

    expect(api.calls).toEqual([{ path: '/auth/logout', body: { refresh_token: 'refresh-1' } }]);
    expect(session.getStatus()).toBe('signedOut');
    expect(store.refreshToken).toBeNull();
    expect(session.getPendingSignIn()).toBeNull();
    expect(await session.getAccessToken()).toBeNull();
  });

  it('asks the web API to clear the cookie on sign-out', async () => {
    const { api, session } = await signedIn(() => 0, 'cookie');
    api.on('/auth/logout', new Response(null, { status: 204 }));

    await session.signOut();

    expect(api.calls).toEqual([{ path: '/auth/logout', body: {} }]);
  });

  it('signs out locally even when the API cannot be reached', async () => {
    const { api, session } = await signedIn(() => 0);
    api.on('/auth/logout', new TypeError('Network request failed'));

    await session.signOut();

    expect(session.getStatus()).toBe('signedOut');
  });
});

describe('AuthSession.restore', () => {
  it('starts signed out without calling the API when the app has no token', async () => {
    const { api, session } = testSession();

    await session.restore();

    expect(session.getStatus()).toBe('signedOut');
    expect(api.calls).toEqual([]);
  });

  it('resumes the session with the stored refresh token', async () => {
    const { api, session } = testSession({ refreshToken: 'refresh-0' });
    api.on('/auth/token/refresh', json(tokens(1)));

    await session.restore();

    expect(session.getStatus()).toBe('signedIn');
    expect(await session.getAccessToken()).toBe('access-1');
  });

  it('asks the web API whether the cookie still holds a session', async () => {
    const { api, session } = testSession({ transport: 'cookie' });
    api.on('/auth/token/refresh', problem('refresh-token-invalid', 401));

    await session.restore();

    expect(session.getStatus()).toBe('signedOut');
    expect(api.calls).toHaveLength(2);
  });

  it('stays signed in when offline at launch, to refresh later', async () => {
    const { api, session, store } = testSession({ refreshToken: 'refresh-0' });
    api.on('/auth/token/refresh', new TypeError('Network request failed'));

    await session.restore();

    expect(session.getStatus()).toBe('signedIn');
    expect(store.refreshToken).toBe('refresh-0');
  });

  it('notifies subscribers when the status changes', async () => {
    const { api, session } = testSession();
    api.on('/auth/otp/request', accepted);
    const listener = jest.fn();
    const unsubscribe = session.subscribe(listener);

    await session.restore();
    unsubscribe();
    await session.requestCode(phone);

    expect(listener).toHaveBeenCalledTimes(1);
  });
});
