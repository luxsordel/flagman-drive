import { describe, expect, it } from 'vitest';

import { createApiClient, type Problem, type TokenPair } from './index';

const json = (body: unknown, status = 200, contentType = 'application/json'): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': contentType } });

describe('createApiClient', () => {
  it('calls typed endpoints against the base URL', async () => {
    const requests: Request[] = [];
    const api = createApiClient({
      baseUrl: 'http://api.test',
      fetch: (request) => {
        requests.push(request);
        return Promise.resolve(json({ status: 'ok' }));
      },
    });

    const { data } = await api.GET('/health');

    expect(data?.status).toBe('ok');
    expect(requests[0]?.url).toBe('http://api.test/health');
    expect(requests[0]?.headers.has('Authorization')).toBe(false);
  });

  it('adds the bearer token when a token getter is given', async () => {
    let authorization: string | null = null;
    const api = createApiClient({
      baseUrl: 'http://api.test',
      getAccessToken: () => Promise.resolve('token-1'),
      fetch: (request) => {
        authorization = request.headers.get('Authorization');
        return Promise.resolve(json({ status: 'ok' }));
      },
    });

    await api.GET('/health');

    expect(authorization).toBe('Bearer token-1');
  });

  it('sends the sign-in body and types the token pair', async () => {
    const tokens: TokenPair = {
      access_token: 'access-1',
      token_type: 'Bearer',
      expires_in: 900,
      refresh_token: 'refresh-1',
    };
    let sentBody: unknown = null;
    const api = createApiClient({
      baseUrl: 'http://api.test',
      fetch: async (request) => {
        sentBody = await request.json();
        return json(tokens);
      },
    });
    const body = {
      phone: '+375291234567',
      code: '123456',
      device_id: '7b6f0a54-3c1e-4d2a-9f5e-2b8c1d0e4f6a',
      refresh_token_transport: 'body' as const,
    };

    const { data } = await api.POST('/auth/otp/verify', { body });

    expect(sentBody).toEqual(body);
    expect(data).toEqual(tokens);
  });

  it('types error responses as Problem', async () => {
    const problem: Problem = { type: 'about:blank', title: 'Internal Server Error', status: 500 };
    const api = createApiClient({
      baseUrl: 'http://api.test',
      fetch: () => Promise.resolve(json(problem, 500, 'application/problem+json')),
    });

    const { error } = await api.GET('/health');

    expect(error).toEqual(problem);
  });
});
