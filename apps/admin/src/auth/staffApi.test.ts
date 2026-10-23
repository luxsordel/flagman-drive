import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { json, problem, sentRequest } from '../test/http';
import { createStaffAuthApi } from './staffApi';

const accessToken = { access_token: 'access-2', token_type: 'Bearer', expires_in: 900 };

function staffAuthApi(...responses: (Response | Error)[]) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>();
  for (const response of responses) {
    if (response instanceof Error) fetch.mockRejectedValueOnce(response);
    else fetch.mockResolvedValueOnce(response);
  }
  const client = createApiClient({ baseUrl: 'http://admin.test', fetch });
  return { api: createStaffAuthApi(client), fetch };
}

const rejection = async (promise: Promise<unknown>): Promise<ApiError> => {
  const error: unknown = await promise.then(
    () => null,
    (reason: unknown) => reason,
  );
  if (!(error instanceof ApiError)) throw new Error('expected an ApiError');
  return error;
};

describe('staff auth API', () => {
  it('checks the password and returns the challenge', async () => {
    const challenge = { challenge_token: 'challenge-1', expires_in: 300, next_step: 'totp' };
    const { api, fetch } = staffAuthApi(json(200, challenge));

    expect(await api.checkPassword('operator@example.com', 'secret password')).toEqual(challenge);

    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/staff/auth/password');
    expect(await request.json()).toEqual({
      email: 'operator@example.com',
      password: 'secret password',
    });
  });

  it('tells wrong credentials by their problem type', async () => {
    const { api } = staffAuthApi(problem(401, 'staff-credentials-invalid'));

    const error = await rejection(api.checkPassword('operator@example.com', 'wrong'));

    expect(error.status).toBe(401);
    expect(error.problem).toBe('staff-credentials-invalid');
  });

  it('reads how long to wait after too many attempts', async () => {
    const { api } = staffAuthApi(problem(429, 'rate-limited', { 'Retry-After': '120' }));

    const error = await rejection(api.checkPassword('operator@example.com', 'wrong'));

    expect(error.problem).toBe('rate-limited');
    expect(error.retryAfterSeconds).toBe(120);
  });

  it('starts the 2FA setup with the challenge', async () => {
    const enrollment = { otpauth_uri: 'otpauth://totp/x?secret=ABC', secret: 'ABC' };
    const { api, fetch } = staffAuthApi(json(200, enrollment));

    expect(await api.startTotpEnrollment('challenge-1')).toEqual(enrollment);
    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/staff/auth/totp/enrollment');
    expect(await request.json()).toEqual({ challenge_token: 'challenge-1' });
  });

  it('exchanges the challenge and the code for an access token', async () => {
    const { api, fetch } = staffAuthApi(json(200, accessToken));

    expect(await api.verifyTotp('challenge-1', '123456')).toEqual({
      accessToken: 'access-2',
      expiresInSeconds: 900,
    });
    expect(await sentRequest(fetch).json()).toEqual({
      challenge_token: 'challenge-1',
      code: '123456',
    });
  });

  it('tells a wrong code from an expired challenge', async () => {
    const { api } = staffAuthApi(
      problem(400, 'totp-invalid'),
      problem(401, 'staff-challenge-invalid'),
    );

    expect((await rejection(api.verifyTotp('challenge-1', '000000'))).problem).toBe('totp-invalid');
    expect((await rejection(api.verifyTotp('challenge-1', '000000'))).problem).toBe(
      'staff-challenge-invalid',
    );
  });

  it('refreshes with the cookie and an empty body, never with a token in the body', async () => {
    const { api, fetch } = staffAuthApi(json(200, accessToken));

    expect(await api.refresh()).toEqual({ accessToken: 'access-2', expiresInSeconds: 900 });
    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/staff/auth/token/refresh');
    expect(await request.json()).toEqual({});
  });

  it('reports a refused refresh cookie as rejected', async () => {
    const { api } = staffAuthApi(problem(401, 'refresh-token-invalid'));

    expect(await api.refresh()).toBe('rejected');
  });

  it('rejects a refresh that failed on the server or the network', async () => {
    const { api } = staffAuthApi(problem(503, 'unavailable'), new TypeError('Failed to fetch'));

    expect((await rejection(api.refresh())).status).toBe(503);
    await expect(api.refresh()).rejects.toThrow('Failed to fetch');
  });

  it('loads the staff member with the given access token', async () => {
    const staff = { id: '0199a7c2-5d1e-7b3a-9f00-000000000001', email: 'a@b.c', role: 'admin' };
    const { api, fetch } = staffAuthApi(json(200, staff));

    expect(await api.me('access-1')).toEqual(staff);
    expect(sentRequest(fetch).headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('signs out with an empty body and rejects when the server fails', async () => {
    const { api, fetch } = staffAuthApi(new Response(null, { status: 204 }), problem(500, 'x'));

    await api.logout();
    expect(new URL(sentRequest(fetch).url).pathname).toBe('/staff/auth/logout');
    expect((await rejection(api.logout())).status).toBe(500);
  });
});
