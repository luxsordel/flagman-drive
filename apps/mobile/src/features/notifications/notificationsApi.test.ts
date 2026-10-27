import { fakeApi, json, problem } from '../auth/session/testing';
import { createNotificationsApi } from './notificationsApi';
import { ok, preferences } from './testing';

function setup() {
  const http = fakeApi();
  return { http, notifications: createNotificationsApi(http.client) };
}

describe('createNotificationsApi', () => {
  it('registers the device with its platform', async () => {
    const { http, notifications } = setup();
    http.on('/me/push-device', new Response(null, { status: 204 }));

    await expect(notifications.registerDevice('ExponentPushToken[abc]', 'ios')).resolves.toEqual(
      ok(null),
    );
    expect(http.calls).toEqual([
      { path: '/me/push-device', body: { token: 'ExponentPushToken[abc]', platform: 'ios' } },
    ]);
  });

  it('reads and changes the preferences', async () => {
    const { http, notifications } = setup();
    http.on(
      '/me/notification-preferences',
      json(preferences()),
      json(preferences(['reservation'])),
    );

    await expect(notifications.getPreferences()).resolves.toEqual(ok(preferences()));
    await expect(notifications.updatePreferences({ reservation: false })).resolves.toEqual(
      ok(preferences(['reservation'])),
    );
    expect(http.calls[1]).toEqual({
      path: '/me/notification-preferences',
      body: { categories: { reservation: false } },
    });
  });

  it.each([
    ['notification-category-mandatory', 422, { kind: 'mandatory' }],
    ['access-token-invalid', 401, { kind: 'sessionExpired' }],
    ['something-new', 400, { kind: 'unknown' }],
  ])('explains %s', async (name, status, error) => {
    const { http, notifications } = setup();
    http.on('/me/notification-preferences', problem(name, status));

    await expect(notifications.updatePreferences({ payment: false })).resolves.toEqual({
      ok: false,
      error,
    });
  });

  it('reads how long to wait after too many registrations', async () => {
    const { http, notifications } = setup();
    http.on('/me/push-device', problem('rate-limited', 429, { 'Retry-After': '120' }));

    await expect(notifications.registerDevice('ExponentPushToken[abc]', 'ios')).resolves.toEqual({
      ok: false,
      error: { kind: 'rateLimited', retryAfterSeconds: 120 },
    });
  });

  it('reports a network failure', async () => {
    const { http, notifications } = setup();
    http.on('/me/push-device', new TypeError('Network request failed'));

    await expect(notifications.registerDevice('ExponentPushToken[abc]', 'ios')).resolves.toEqual({
      ok: false,
      error: { kind: 'network' },
    });
  });
});
