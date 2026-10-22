import 'i18next';

import type { ru } from './locales/ru';

// Typed keys: `t('auth.login.title')` is checked at compile time.
declare module 'i18next' {
  interface CustomTypeOptions {
    resources: { translation: typeof ru };
  }
}
