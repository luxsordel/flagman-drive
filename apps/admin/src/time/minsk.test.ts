import { describe, expect, it } from 'vitest';

import { formatMinskTime } from './minsk';

describe('Minsk time', () => {
  it('shows a UTC moment in Minsk time, not in the zone of the computer', () => {
    expect(formatMinskTime('2026-10-23T22:04:05Z', 'ru')).toBe('24.10.2026, 01:04:05');
  });

  it('formats each language by its own rules', () => {
    expect(formatMinskTime('2026-10-23T22:04:05Z', 'ru')).toBe('24.10.2026, 01:04:05');
    expect(formatMinskTime('2026-10-23T22:04:05Z', 'en')).toBe('10/24/26, 1:04:05 AM');
  });
});
