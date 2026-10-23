import { describe, expect, it } from 'vitest';

import { ApiError, toApiError, unwrap } from './client';

const response = (status: number, headers: Record<string, string> = {}): Response =>
  new Response(null, { status, headers });

describe('API errors', () => {
  it('keeps the Problem type without the project prefix', () => {
    const error = toApiError(response(400), {
      type: 'urn:flagman:problem:totp-invalid',
      title: 'Wrong code',
      status: 400,
    });

    expect(error.status).toBe(400);
    expect(error.problem).toBe('totp-invalid');
  });

  it('ignores Problem types from elsewhere and bodies that are not a Problem', () => {
    expect(toApiError(response(400), { type: 'about:blank' }).problem).toBeUndefined();
    expect(toApiError(response(400), 'Bad Gateway').problem).toBeUndefined();
    expect(toApiError(response(400), undefined).problem).toBeUndefined();
  });

  it('reads Retry-After in seconds and ignores a date or garbage', () => {
    expect(toApiError(response(429, { 'Retry-After': '90' }), null).retryAfterSeconds).toBe(90);
    for (const garbage of ['1e2', '0x10', '-5', '0']) {
      expect(
        toApiError(response(429, { 'Retry-After': garbage }), null).retryAfterSeconds,
        garbage,
      ).toBeUndefined();
    }
    expect(
      toApiError(response(429, { 'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT' }), null)
        .retryAfterSeconds,
    ).toBeUndefined();
    expect(toApiError(response(429), null).retryAfterSeconds).toBeUndefined();
  });

  it('unwraps the data of a success and throws the ApiError of a failure', () => {
    expect(unwrap({ data: { ok: true }, response: response(200) })).toEqual({ ok: true });
    expect(() =>
      unwrap({ error: { type: 'urn:flagman:problem:forbidden' }, response: response(403) }),
    ).toThrow(ApiError);
  });

  it('tells a stopped API behind a proxy from an answer of the API', () => {
    expect(new ApiError(502).unavailable).toBe(true);
    expect(new ApiError(504).unavailable).toBe(true);
    expect(new ApiError(503, 'sign-in-unavailable').unavailable).toBe(false);
    expect(new ApiError(500).unavailable).toBe(false);
  });
});
