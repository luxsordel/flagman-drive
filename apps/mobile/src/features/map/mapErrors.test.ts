import { mapErrorFromResponse } from './mapErrors';

const problem = (slug: string) => ({
  type: `urn:flagman:problem:${slug}`,
  title: 't',
  status: 400,
});

describe('mapErrorFromResponse', () => {
  it.each([
    ['access-token-invalid', 'sessionExpired'],
    ['rate-limited', 'rateLimited'],
    ['car-not-found', 'carGone'],
    ['validation-error', 'unknown'],
  ])('maps %s to %s', (slug, kind) => {
    expect(mapErrorFromResponse(problem(slug))).toEqual({ kind });
  });

  it('treats a body without a problem as unknown', () => {
    expect(mapErrorFromResponse('<html>')).toEqual({ kind: 'unknown' });
  });
});
