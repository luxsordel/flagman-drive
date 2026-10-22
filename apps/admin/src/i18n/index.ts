import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './en.json';
import ru from './ru.json';

export const languages = ['ru', 'en'] as const;
export type Language = (typeof languages)[number];
export const defaultLanguage: Language = 'ru';

export const i18n = i18next.createInstance();

// Resources are bundled, so initialization is synchronous and the first render is already translated.
void i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru }, en: { translation: en } },
  lng: defaultLanguage,
  fallbackLng: defaultLanguage,
  supportedLngs: languages,
  interpolation: { escapeValue: false },
  initAsync: false,
});

const syncDocumentLanguage = (language: string): void => {
  document.documentElement.lang = language;
};
syncDocumentLanguage(i18n.language);
i18n.on('languageChanged', syncDocumentLanguage);
