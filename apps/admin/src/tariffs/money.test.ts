import { describe, expect, it } from 'vitest';

import { formatKopecks, kopecksToRubles, parseRubles } from './money';

describe('money', () => {
  it.each([
    ['0,39', 39],
    ['0.39', 39],
    ['0.29', 29],
    ['12', 1200],
    ['12,5', 1250],
    ['1 200,50', 120050],
    [' 30 ', 3000],
    ['0', 0],
  ])('reads %j as %d kopecks', (text, kopecks) => {
    expect(parseRubles(text)).toBe(kopecks);
  });

  it.each(['', 'abc', '0,399', '-1', '1e3', '1,2,3', '12345678'])('refuses %j', (text) => {
    expect(parseRubles(text)).toBeNull();
  });

  it.each([
    [39, '0.39'],
    [3000, '30.00'],
    [5, '0.05'],
    [0, '0.00'],
  ])('shows %d kopecks as %j for editing', (kopecks, text) => {
    expect(kopecksToRubles(kopecks)).toBe(text);
    expect(parseRubles(text)).toBe(kopecks);
  });

  it('formats kopecks as rubles of the language', () => {
    // Intl puts a no-break space between the amount and the currency.
    expect(formatKopecks(39, 'en').replace(/\s/g, ' ')).toBe('BYN 0.39');
    expect(formatKopecks(3000, 'ru').replace(/\s/g, ' ')).toBe('30,00 BYN');
  });
});
