import { detectLanguage, languageNames, languages, resources } from './index';

/** Dotted paths of every string, e.g. `auth.login.title`. */
function keys(tree: object, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    typeof value === 'string' ? [`${prefix}${key}`] : keys(value as object, `${prefix}${key}.`),
  );
}

describe('locales', () => {
  // The Translation type already catches missing keys at compile time; this also catches
  // keys that exist only in English.
  it('ru and en have the same keys', () => {
    expect(keys(resources.en.translation).sort()).toEqual(keys(resources.ru.translation).sort());
  });

  it('has no empty strings', () => {
    for (const { translation } of Object.values(resources)) {
      const values = keys(translation).map((path) =>
        path
          .split('.')
          .reduce<unknown>((node, key) => (node as Record<string, unknown>)[key], translation),
      );
      expect(values.every((value) => typeof value === 'string' && value.trim() !== '')).toBe(true);
    }
  });
});

describe('languages', () => {
  it('lists every bundled language, each with its own name', () => {
    expect([...languages].sort()).toEqual(Object.keys(resources).sort());
    expect(Object.keys(languageNames).sort()).toEqual(Object.keys(resources).sort());
  });
});

describe('detectLanguage', () => {
  it('picks the first supported device language', () => {
    expect(
      detectLanguage([{ languageCode: 'de' }, { languageCode: 'en' }, { languageCode: 'ru' }]),
    ).toBe('en');
  });

  it('falls back to Russian', () => {
    expect(detectLanguage([{ languageCode: 'be' }, { languageCode: null }])).toBe('ru');
    expect(detectLanguage([])).toBe('ru');
  });
});
