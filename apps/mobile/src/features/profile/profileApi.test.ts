import { fakeApi, json, problem } from '../auth/session/testing';
import { createProfileApi } from './profileApi';
import { ok, profile } from './testing';

function setup() {
  const http = fakeApi();
  return { http, profiles: createProfileApi(http.client) };
}

describe('createProfileApi', () => {
  it('reads the profile', async () => {
    const { http, profiles } = setup();
    http.on('/me', json(profile()));

    await expect(profiles.getProfile()).resolves.toEqual(ok(profile()));
  });

  it('sends only the changed fields and returns what the API kept', async () => {
    const { http, profiles } = setup();
    http.on('/me', json(profile({ email: 'anna@example.com' })));

    await expect(profiles.updateProfile({ email: 'Anna@Example.com' })).resolves.toEqual(
      ok(profile({ email: 'anna@example.com' })),
    );
    expect(http.calls).toEqual([{ path: '/me', body: { email: 'Anna@Example.com' } }]);
  });

  it('explains an error the API described', async () => {
    const { http, profiles } = setup();
    http.on('/me', problem('birth-date-invalid', 422));

    await expect(profiles.updateProfile({ birth_date: '1800-01-01' })).resolves.toEqual({
      ok: false,
      error: { kind: 'birthDateInvalid' },
    });
  });

  it('reports an answer without Problem Details as unknown', async () => {
    const { http, profiles } = setup();
    http.on('/me', new Response('<html>Bad Gateway</html>', { status: 502 }));

    await expect(profiles.getProfile()).resolves.toEqual({
      ok: false,
      error: { kind: 'unknown' },
    });
  });

  it('reports a network error when the API cannot be reached', async () => {
    const { http, profiles } = setup();
    http.on('/me', new TypeError('Network request failed'));

    await expect(profiles.getProfile()).resolves.toEqual({
      ok: false,
      error: { kind: 'network' },
    });
  });
});
