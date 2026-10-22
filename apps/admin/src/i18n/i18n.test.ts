import { describe, expect, it } from 'vitest';

import en from './en.json';
import ru from './ru.json';

type Dictionary = { [key: string]: string | Dictionary };

const flatten = (dictionary: Dictionary, prefix = ''): [string, string][] =>
  Object.entries(dictionary).flatMap(([key, value]): [string, string][] =>
    typeof value === 'string' ? [[prefix + key, value]] : flatten(value, `${prefix}${key}.`),
  );

const keys = (entries: [string, string][]): string[] => entries.map(([key]) => key).sort();

describe('translations', () => {
  const russian = flatten(ru);
  const english = flatten(en);

  it('have the same keys in ru and en', () => {
    expect(keys(english)).toEqual(keys(russian));
  });

  it('have no empty strings', () => {
    for (const [key, text] of [...russian, ...english]) {
      expect(text.trim(), key).not.toBe('');
    }
  });
});
