import type { ThemeName } from '@flagman/design-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth, useStaff } from '../auth/context';
import { defaultLanguage, languages } from '../i18n';
import { applyTheme, currentTheme } from '../theme';

export function Header() {
  const { t, i18n } = useTranslation();
  const staff = useStaff();
  const { signOut } = useAuth();
  const [theme, setTheme] = useState<ThemeName>(currentTheme);

  const toggleTheme = (): void => {
    const next = theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setTheme(next);
  };

  return (
    <header className="header">
      <span className="header-title">{t('app.subtitle')}</span>
      <div className="header-controls">
        <label className="inline-label">
          {t('header.language')}
          <select
            className="field"
            value={i18n.resolvedLanguage ?? defaultLanguage}
            onChange={(event) => void i18n.changeLanguage(event.target.value)}
          >
            {languages.map((language) => (
              <option key={language} value={language}>
                {t(`languages.${language}`)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button"
          aria-pressed={theme === 'dark'}
          onClick={toggleTheme}
        >
          {t('header.darkTheme')}
        </button>
        <span className="header-staff">
          <span>{staff.email}</span>
          <span className="muted">{t(`roles.${staff.role}`)}</span>
        </span>
        {/* The session ends locally even if the API is unreachable; RequireAuth then opens /login. */}
        <button type="button" className="button" onClick={() => void signOut()}>
          {t('header.signOut')}
        </button>
      </div>
    </header>
  );
}
