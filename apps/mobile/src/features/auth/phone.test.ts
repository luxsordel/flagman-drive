import { parseBelarusMobile } from './phone';

describe('parseBelarusMobile', () => {
  it.each([
    ['291234567', '+375291234567'],
    ['29 123-45-67', '+375291234567'],
    ['(44) 765 43 21', '+375447654321'],
    ['+375 33 111 22 33', '+375331112233'],
    ['8 025 999 00 11', '+375259990011'],
  ])('accepts %s', (input, expected) => {
    expect(parseBelarusMobile(input)).toBe(expected);
  });

  it.each([
    ['', 'empty'],
    ['29123456', 'too short'],
    ['2912345678', 'too long'],
    ['171234567', 'Minsk landline, cannot receive SMS'],
    ['+7 912 345 67 89', 'foreign number'],
  ])('rejects %s (%s)', (input) => {
    expect(parseBelarusMobile(input)).toBeNull();
  });
});
