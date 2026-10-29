import {
  clockOffsetMs,
  distanceMeters,
  formatCountdown,
  isActive,
  reservationPhase,
} from './rentalTime';
import { rental } from './testing';

const at = (iso: string) => Date.parse(iso);

describe('reservationPhase', () => {
  const reserved = rental();

  it('is free until the free wait ends', () => {
    expect(reservationPhase(reserved, at('2026-10-26T12:09:30Z'))).toEqual({
      kind: 'free',
      leftMs: 30_000,
    });
  });

  it('is paid from the end of the free wait until the reservation ends', () => {
    expect(reservationPhase(reserved, at('2026-10-26T12:10:00Z'))).toEqual({
      kind: 'paid',
      leftMs: 30 * 60_000,
    });
  });

  it('is over at the end exactly', () => {
    expect(reservationPhase(reserved, at('2026-10-26T12:40:00Z'))).toEqual({ kind: 'over' });
  });
});

describe('clockOffsetMs', () => {
  it('is how far the server is ahead of the phone', () => {
    expect(clockOffsetMs('2026-10-26T12:00:05Z', at('2026-10-26T12:00:00Z'))).toBe(5000);
  });
});

describe('formatCountdown', () => {
  it.each([
    [0, '0:00'],
    [1, '0:01'],
    [59_001, '1:00'],
    [15 * 60_000, '15:00'],
    [3_600_000 + 61_000, '1:01:01'],
    [-5000, '0:00'],
  ])('%d ms is %s', (ms, text) => {
    expect(formatCountdown(ms)).toBe(text);
  });
});

describe('distanceMeters', () => {
  it('is about 111 m per thousandth of a degree of latitude', () => {
    const meters = distanceMeters({ lat: 53.9, lon: 27.56 }, { lat: 53.901, lon: 27.56 });
    expect(meters).toBeGreaterThan(110);
    expect(meters).toBeLessThan(112);
  });
});

describe('isActive', () => {
  it.each([
    ['reserved', true],
    ['in_trip', true],
    ['parked', true],
    ['cancelled', false],
    ['finished', false],
    ['paid', false],
  ] as const)('%s is active: %s', (status, active) => {
    expect(isActive(rental({ status }))).toBe(active);
  });
});
