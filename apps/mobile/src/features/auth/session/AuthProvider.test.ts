import { resolveApiUrl } from './AuthProvider';

describe('resolveApiUrl', () => {
  it.each([undefined, '', '  '])('falls back to the local API for %p', (configured) => {
    expect(resolveApiUrl(configured)).toBe('http://localhost:8000');
  });

  it('uses the configured origin', () => {
    expect(resolveApiUrl('http://10.0.2.2:8000')).toBe('http://10.0.2.2:8000');
  });
});
