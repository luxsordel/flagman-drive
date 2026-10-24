import { describe, expect, it } from 'vitest';

import { loginRedirect, redirectTarget } from './redirect';

describe('redirect after sign-in', () => {
  it('returns to the page with its query', () => {
    expect(redirectTarget(loginRedirect({ pathname: '/audit', search: '?action=x' }))).toBe(
      '/audit?action=x',
    );
  });

  it('opens the dashboard for anything that is not a path inside the panel', () => {
    for (const state of [
      null,
      undefined,
      'x',
      {},
      { from: 42 },
      { from: '//evil.example' },
      { from: '/\\evil.example' },
      { from: '/\t/evil.example' },
      { from: '/\n/evil.example' },
      { from: '/\r\\evil.example' },
      { from: '/a/../\t/evil.example' },
      { from: '//[' },
      { from: '//x:y' },
    ]) {
      expect(redirectTarget(state)).toBe('/');
    }
    expect(redirectTarget({ from: 'https://evil.example' })).toBe('/');
  });
});
