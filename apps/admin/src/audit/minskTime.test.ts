import { describe, expect, it } from 'vitest';

import { formatMinskTime, isCalendarDay, minskDayStart, nextDay } from './minskTime';

describe('Minsk time', () => {
  it('accepts only real calendar days', () => {
    expect(isCalendarDay('2026-10-23')).toBe(true);
    expect(isCalendarDay('2028-02-29')).toBe(true);
    for (const value of ['2026-02-29', '2026-02-30', '2026-13-45', '2026-1-5', 'yesterday', '']) {
      expect(isCalendarDay(value), value).toBe(false);
    }
  });

  it('starts a Minsk day at midnight with the Minsk offset', () => {
    expect(minskDayStart('2026-10-23')).toBe('2026-10-23T00:00:00+03:00');
    // Belarus left daylight saving in 2011: winter has the same offset.
    expect(minskDayStart('2026-01-15')).toBe('2026-01-15T00:00:00+03:00');
  });

  it('moves to the next day across months and years', () => {
    expect(nextDay('2026-10-23')).toBe('2026-10-24');
    expect(nextDay('2026-02-28')).toBe('2026-03-01');
    expect(nextDay('2026-12-31')).toBe('2027-01-01');
  });

  it('shows a UTC moment in Minsk time, not in the zone of the computer', () => {
    expect(formatMinskTime('2026-10-23T22:04:05Z', 'ru')).toBe('24.10.2026, 01:04:05');
  });

  it('formats each language by its own rules', () => {
    expect(formatMinskTime('2026-10-23T22:04:05Z', 'ru')).toBe('24.10.2026, 01:04:05');
    expect(formatMinskTime('2026-10-23T22:04:05Z', 'en')).toBe('10/24/26, 1:04:05 AM');
  });
});
