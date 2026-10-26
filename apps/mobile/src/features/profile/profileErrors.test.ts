import { profileErrorFromResponse } from './profileErrors';

const problem = (slug: string) => ({ type: `urn:flagman:problem:${slug}`, status: 422 });

describe('profileErrorFromResponse', () => {
  it.each([
    ['access-token-invalid', 'sessionExpired'],
    ['validation-error', 'invalid'],
    ['birth-date-invalid', 'birthDateInvalid'],
    ['kyc-locked', 'locked'],
  ])('maps the problem %s to %s', (slug, kind) => {
    expect(profileErrorFromResponse(problem(slug))).toEqual({ kind });
  });

  it.each([null, '', 'Bad Gateway', { type: 'about:blank' }])(
    'reports %j, which is not Problem Details, as unknown',
    (body) => {
      expect(profileErrorFromResponse(body)).toEqual({ kind: 'unknown' });
    },
  );
});
