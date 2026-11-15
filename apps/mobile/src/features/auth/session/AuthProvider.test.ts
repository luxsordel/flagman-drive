import { resolveApiUrl, webApiUrl, resolveAppApiUrl } from './AuthProvider';

describe('resolveApiUrl', () => {
  it.each([undefined, '', '  '])('falls back to the local API for %p', (configured) => {
    expect(resolveApiUrl(configured)).toBe('http://localhost:8000');
  });

  it('uses the configured origin', () => {
    expect(resolveApiUrl('http://10.0.2.2:8000')).toBe('http://10.0.2.2:8000');
  });

  it('uses the current web origin for a stand bundle', () => {
    expect(webApiUrl({ origin: 'https://app.stand.example' })).toBe('https://app.stand.example');
  });
});

describe('resolveAppApiUrl', () => {
  it('falls back to localhost:8000 on web when API_URL is empty and sameOrigin is false', () => {
    expect(resolveAppApiUrl(undefined, false, 'web', 'https://app.stand.example')).toBe(
      'http://localhost:8000',
    );
  });

  it('falls back to localhost:8000 on web when API_URL is empty and sameOrigin is true but platform is not web', () => {
    expect(resolveAppApiUrl(undefined, true, 'ios', 'https://app.stand.example')).toBe(
      'http://localhost:8000',
    );
  });

  it('returns the window origin when sameOrigin is true on web with no configured URL', () => {
    expect(resolveAppApiUrl(undefined, true, 'web', 'https://app.stand.example')).toBe(
      'https://app.stand.example',
    );
  });

  it('prefers configured URL over sameOrigin flag', () => {
    expect(resolveAppApiUrl('http://10.0.2.2:8000', true, 'web', 'https://app.stand.example')).toBe(
      'http://10.0.2.2:8000',
    );
  });
});
