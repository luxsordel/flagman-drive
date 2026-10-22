import type ru from './ru.json';

// Typed keys: t('nav.fleet') compiles, a missing or misspelled key does not.
declare module 'i18next' {
  interface CustomTypeOptions {
    resources: { translation: typeof ru };
  }
}
