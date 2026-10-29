import { isRetryable, isStale, rentalErrorFromResponse } from './rentalErrors';

const problem = (slug: string) => ({ type: `urn:flagman:problem:${slug}` });

describe('rentalErrorFromResponse', () => {
  it.each([
    ['kyc-not-approved', 'kycNotApproved'],
    ['client-blocked', 'blocked'],
    ['car-not-found', 'carGone'],
    ['active-rental-exists', 'activeRental'],
    ['reservation-expired', 'expired'],
    ['rental-command-in-progress', 'busy'],
    ['inspection-required', 'inspectionRequired'],
    ['inspection-photo-too-large', 'tooLarge'],
    ['too-far-from-car', 'tooFar'],
    ['car-location-unknown', 'carLocationUnknown'],
    ['car-command-rejected', 'carRefused'],
    ['telematics-unavailable', 'carUnreachable'],
    ['telematics-timeout', 'carTimeout'],
    ['something-new', 'unknown'],
  ])('%s is %s', (slug, kind) => {
    expect(rentalErrorFromResponse(problem(slug), 409, null)).toEqual({ kind });
  });

  it('reads the wait of a rate limit', () => {
    expect(rentalErrorFromResponse(problem('rate-limited'), 429, '42')).toEqual({
      kind: 'rateLimited',
      retryAfter: 42,
    });
  });

  it('takes a bare 413 of a proxy for a photo too large', () => {
    expect(rentalErrorFromResponse('<html>', 413, null)).toEqual({ kind: 'tooLarge' });
  });
});

describe('error groups', () => {
  it('retries what may pass by itself, not what the client must change', () => {
    expect(isRetryable({ kind: 'network' })).toBe(true);
    expect(isRetryable({ kind: 'carUnreachable' })).toBe(true);
    expect(isRetryable({ kind: 'tooFar' })).toBe(false);
    expect(isRetryable({ kind: 'carTimeout' })).toBe(false);
  });

  it('reloads the rental when the server has moved on, also after a timeout', () => {
    expect(isStale({ kind: 'expired' })).toBe(true);
    expect(isStale({ kind: 'carTimeout' })).toBe(true);
    expect(isStale({ kind: 'tooFar' })).toBe(false);
  });
});
