import { formatDistance, formatKopecks } from './mapFormat';

describe('formatKopecks', () => {
  it('shows kopecks as rubles of the language', () => {
    // Intl puts a no-break space between the amount and the currency.
    expect(formatKopecks(39, 'ru').replace(/\s/g, ' ')).toBe('0,39 BYN');
    expect(formatKopecks(1250, 'en').replace(/\s/g, ' ')).toBe('BYN 12.50');
  });
});

describe('formatDistance', () => {
  it.each([
    [0, '0'],
    [123, '120'],
    [994, '990'],
  ])('shows %d m in steps of 10 m', (meters, value) => {
    expect(formatDistance(meters, 'ru')).toEqual({ unit: 'meters', value });
  });

  it('switches to kilometers with one decimal, in the language of the app', () => {
    expect(formatDistance(995, 'ru')).toEqual({ unit: 'kilometers', value: '1' });
    expect(formatDistance(1540, 'ru')).toEqual({ unit: 'kilometers', value: '1,5' });
    expect(formatDistance(1540, 'en')).toEqual({ unit: 'kilometers', value: '1.5' });
  });
});
