import { useState, type SyntheticEvent } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Staff sign-in form: email, password and a TOTP code (docs/03-security.md).
 * A stub until the identity module exists: it never sends or stores what was typed.
 * `noValidate`: browser validation messages ignore the app language, ours come with the real sign-in.
 */
export function LoginPage() {
  const { t } = useTranslation();
  const [submitted, setSubmitted] = useState(false);

  const onSubmit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>): void => {
    event.preventDefault();
    setSubmitted(true);
  };

  return (
    <main className="login">
      <form className="card login-card" noValidate onSubmit={onSubmit}>
        <div className="brand">{t('app.name')}</div>
        <h1>{t('login.title')}</h1>
        <label className="stacked-label">
          {t('login.email')}
          <input className="field" type="email" autoComplete="username" required />
        </label>
        <label className="stacked-label">
          {t('login.password')}
          <input className="field" type="password" autoComplete="current-password" required />
        </label>
        <label className="stacked-label">
          {t('login.totp')}
          <input
            className="field"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            required
          />
        </label>
        <button type="submit" className="button button-primary">
          {t('login.submit')}
        </button>
        <p role="status" className="login-status">
          {submitted ? t('login.notConnected') : ''}
        </p>
      </form>
    </main>
  );
}
