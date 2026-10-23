import { isRetryable, kycErrorFromResponse } from './kycErrors';

const problem = (slug: string) => ({ type: `urn:flagman:problem:${slug}`, title: slug });

describe('kycErrorFromResponse', () => {
  it.each([
    ['access-token-invalid', 401, 'sessionExpired'],
    ['document-too-large', 413, 'tooLarge'],
    ['document-type-unsupported', 415, 'unsupported'],
    ['kyc-locked', 409, 'locked'],
    ['kyc-incomplete', 409, 'incomplete'],
    ['document-storage-unavailable', 503, 'storageUnavailable'],
    ['something-new', 400, 'unknown'],
  ])('maps %s to %s', (slug, status, kind) => {
    expect(kycErrorFromResponse(problem(slug), status, null)).toEqual({ kind });
  });

  it('keeps the wait of a rate limit', () => {
    expect(kycErrorFromResponse(problem('rate-limited'), 429, '42')).toEqual({
      kind: 'rateLimited',
      retryAfter: 42,
    });
    expect(kycErrorFromResponse(problem('rate-limited'), 429, 'soon')).toEqual({
      kind: 'rateLimited',
    });
  });

  it('treats a bare 413 from a proxy as a too large photo', () => {
    expect(kycErrorFromResponse('<html>Request Entity Too Large</html>', 413, null)).toEqual({
      kind: 'tooLarge',
    });
    expect(kycErrorFromResponse(null, 502, null)).toEqual({ kind: 'unknown' });
  });
});

describe('isRetryable', () => {
  it('offers to send the same photo again only when that can help', () => {
    expect(isRetryable({ kind: 'network' })).toBe(true);
    expect(isRetryable({ kind: 'storageUnavailable' })).toBe(true);
    expect(isRetryable({ kind: 'tooLarge' })).toBe(false);
    expect(isRetryable({ kind: 'unsupported' })).toBe(false);
    expect(isRetryable({ kind: 'locked' })).toBe(false);
  });
});
