import { describe, expect, it } from 'vitest';

import { formatCalendarDay, formatWaiting, fullName, reasonLength } from './format';

describe('KYC formatting', () => {
  it('names the client as on the documents and tells when there is no name', () => {
    expect(fullName({ first_name: 'Ivan', last_name: 'Petrov' })).toBe('Petrov Ivan');
    expect(fullName({ first_name: 'Ivan', last_name: null })).toBe('Ivan');
    expect(fullName({ first_name: null, last_name: null })).toBeNull();
  });

  it('counts the reason in characters after trimming, as the API does', () => {
    expect(reasonLength('  blurry  ')).toBe(6);
    // Two UTF-16 units, one character.
    expect(reasonLength('😀')).toBe(1);
  });

  it('shows a calendar day without moving it across time zones', () => {
    expect(formatCalendarDay('1990-05-17', 'ru')).toBe('17 мая 1990 г.');
    expect(formatCalendarDay('1990-05-17', 'en')).toBe('May 17, 1990');
  });

  it('shows the waiting time in minutes, hours, then days', () => {
    const now = new Date('2026-10-24T12:00:00Z');
    expect(formatWaiting('2026-10-24T11:35:00Z', now, 'en')).toBe('25 minutes ago');
    expect(formatWaiting('2026-10-23T09:00:00Z', now, 'en')).toBe('27 hours ago');
    expect(formatWaiting('2026-10-20T12:00:00Z', now, 'ru')).toBe('4 дня назад');
    // A clock behind the server's must not show a negative wait.
    expect(formatWaiting('2026-10-24T12:01:00Z', now, 'en')).toBe('0 minutes ago');
  });
});
