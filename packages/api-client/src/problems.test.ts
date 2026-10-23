import { describe, expect, it } from 'vitest';

import { parseRetryAfter, problemSlug } from './index';

describe('problemSlug', () => {
  it('returns the slug of a FLAGMAN problem type', () => {
    expect(problemSlug({ type: 'urn:flagman:problem:kyc-locked', status: 409 })).toBe('kyc-locked');
  });

  it.each([
    ['null', null],
    ['a string body', '<html>Bad Gateway</html>'],
    ['an object without type', { status: 500 }],
    ['a non-string type', { type: 42 }],
    ['a foreign type', { type: 'about:blank' }],
  ])('returns null for %s', (_, body) => {
    expect(problemSlug(body)).toBeNull();
  });
});

describe('parseRetryAfter', () => {
  it('reads delta-seconds, ignoring surrounding spaces', () => {
    expect(parseRetryAfter('30')).toBe(30);
    expect(parseRetryAfter(' 0 ')).toBe(0);
  });

  it.each([
    ['a missing header', null],
    ['an empty header', ''],
    ['an HTTP date', 'Wed, 21 Oct 2026 07:28:00 GMT'],
    ['a negative number', '-5'],
    ['a fraction', '1.5'],
    ['garbage', 'soon'],
  ])('returns null for %s', (_, value) => {
    expect(parseRetryAfter(value)).toBeNull();
  });
});
