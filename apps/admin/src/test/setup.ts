import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

import { defaultLanguage, i18n } from '../i18n';

// jsdom has no matchMedia; tests run as if the system prefers the light theme.
vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query }));

// Vitest runs without globals, so Testing Library cannot register its own cleanup.
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage(defaultLanguage);
  delete document.documentElement.dataset.theme;
});
