import { getLocales, type Locale } from 'expo-localization';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';

import { en } from './locales/en';
import { ru } from './locales/ru';

export const resources = {
  ru: { translation: ru },
  en: { translation: en },
} as const;

export type Language = keyof typeof resources;

// The service launches in Minsk, so Russian is the default for any unsupported device language.
export const defaultLanguage: Language = 'ru';

export const languages = ['ru', 'en'] as const satisfies readonly Language[];

/** Each language named in itself, so a client finds theirs whatever the app speaks now. */
export const languageNames: Readonly<Record<Language, string>> = { ru: 'Русский', en: 'English' };

const isLanguage = (code: string | null): code is Language =>
  code !== null && Object.hasOwn(resources, code);

/** First supported language from the device preference list. */
export function detectLanguage(locales: readonly Pick<Locale, 'languageCode'>[]): Language {
  return locales.map((locale) => locale.languageCode).find(isLanguage) ?? defaultLanguage;
}

export function initI18n(language: Language = detectLanguage(getLocales())): typeof i18next {
  if (!i18next.isInitialized) {
    // Resources are bundled, so synchronous init avoids a frame of untranslated keys.
    void i18next.use(initReactI18next).init({
      resources,
      lng: language,
      fallbackLng: defaultLanguage,
      initAsync: false,
      interpolation: { escapeValue: false },
    });
  }
  return i18next;
}
