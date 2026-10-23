import { lightPalette } from '@flagman/design-tokens';
import { useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { AuthLoading } from '../auth/guards';
import { redirectTarget } from '../auth/redirect';
import type { TotpEnrollment } from '../auth/staffApi';
import type ru from '../i18n/ru.json';

// The limits of the API schemas (identity/domain.py). Input is checked here, never cut by
// `maxLength`: the browser cuts a paste before onChange sees it, and a cut password or code
// only fails later as a wrong one.
const emailMaxLength = 254;
// Printable ASCII without spaces around one "@", as EMAIL_PATTERN on the server.
const emailPattern = /^[!-?A-~]+@[!-?A-~]+$/;
const passwordMaxLength = 128;
const totpCodeLength = 6;

// The challenge token proves the password step; it lives only in this state (docs/03-security.md).
type Step =
  | { name: 'password' }
  // 2FA is not set up and getting the QR code failed: retry with the same challenge,
  // not the password.
  | { name: 'enroll'; challengeToken: string }
  // `enrollment`: 2FA is not set up yet, so the QR code comes before the first code.
  | { name: 'code'; challengeToken: string; enrollment: TotpEnrollment | null }
  // The challenge may be spent and a session may be in the refresh cookie: the code was accepted
  // but the profile did not load, or the answer to the code was lost. Only a reload (which
  // restores a session if there is one) can go on.
  | { name: 'reload' };

type ErrorKey = keyof typeof ru.login.errors;

interface Failure {
  key: ErrorKey;
  minutes?: number;
  /** The challenge is no longer usable: only the password step can go on. */
  restart?: true;
}

function describeFailure(error: unknown): Failure {
  // fetch rejects with a TypeError when the request does not get through; anything else that is
  // not an ApiError is a bug, not the network.
  if (error instanceof TypeError) return { key: 'network' };
  if (!(error instanceof ApiError)) return { key: 'unexpected' };
  if (error.unavailable) return { key: 'network' };
  switch (error.problem) {
    case 'staff-credentials-invalid':
      return { key: 'credentials' };
    case 'totp-invalid':
      return { key: 'totpInvalid' };
    case 'rate-limited':
      return error.retryAfterSeconds
        ? { key: 'rateLimitedFor', minutes: Math.ceil(error.retryAfterSeconds / 60) }
        : { key: 'rateLimited' };
    case 'staff-challenge-invalid':
    case 'totp-enrollment-not-started':
      return { key: 'challengeExpired', restart: true };
    case 'totp-already-enrolled':
      return { key: 'alreadyEnrolled', restart: true };
    case 'totp-reset-required':
      return { key: 'resetRequired', restart: true };
    default:
      return { key: 'unexpected' };
  }
}

const submitLabel = {
  password: 'login.next',
  enroll: 'login.retry',
  code: 'login.submit',
} as const satisfies Record<Exclude<Step['name'], 'reload'>, `login.${keyof typeof ru.login}`>;

/** The secret in groups of four, as authenticator apps show it for typing by hand. */
const groupSecret = (secret: string): string => secret.match(/.{1,4}/g)?.join(' ') ?? secret;

/**
 * Staff sign-in (docs/03-security.md): email and password, then the TOTP code. A staff member
 * without 2FA first scans the QR code; the first accepted code finishes the setup.
 * `noValidate`: browser validation messages ignore the app language, so the form checks
 * fields itself.
 */
export function LoginPage() {
  const { t } = useTranslation();
  const { state, signIn, signOut } = useAuth();
  const { staffSignIn } = useApis();
  const location = useLocation();
  const [step, setStep] = useState<Step>({ name: 'password' });
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [failure, setFailure] = useState<Failure | null>(null);
  const [pending, setPending] = useState(false);
  const codeInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step.name === 'code') codeInput.current?.focus();
  }, [step]);

  if (state.status === 'loading') return <AuthLoading />;
  if (state.status === 'signedIn') return <Navigate to={redirectTarget(location.state)} replace />;

  const fail = (error: unknown): void => {
    const next = describeFailure(error);
    setFailure(next);
    setCode('');
    if (next.restart) setStep({ name: 'password' });
  };

  const submitPassword = async (): Promise<void> => {
    if (!email.trim() || !password) {
      setFailure({ key: 'missingCredentials' });
      return;
    }
    if (!emailPattern.test(email.trim())) {
      setFailure({ key: 'emailFormat' });
      return;
    }
    // No account can have these: staff accounts are created within the same limits.
    if (email.trim().length > emailMaxLength || password.length > passwordMaxLength) {
      setFailure({ key: 'credentials' });
      return;
    }
    const challenge = await staffSignIn.checkPassword(email.trim(), password);
    // The password is not needed past this step; do not keep it in memory.
    setPassword('');
    if (challenge.next_step === 'totp_enrollment') {
      setStep({ name: 'enroll', challengeToken: challenge.challenge_token });
      await enroll(challenge.challenge_token);
    } else {
      setStep({ name: 'code', challengeToken: challenge.challenge_token, enrollment: null });
    }
  };

  const enroll = async (challengeToken: string): Promise<void> => {
    const enrollment = await staffSignIn.startTotpEnrollment(challengeToken);
    setStep({ name: 'code', challengeToken, enrollment });
  };

  const submitCode = async (challengeToken: string): Promise<void> => {
    // onChange keeps at most six digits, so the length is the whole check.
    if (code.length !== totpCodeLength) {
      setFailure({ key: 'codeFormat' });
      return;
    }
    let token;
    try {
      token = await staffSignIn.verifyTotp(challengeToken, code);
    } catch (error) {
      // The API may have accepted the code and set the session cookie before the answer was lost.
      if (describeFailure(error).key !== 'network') throw error;
      setStep({ name: 'reload' });
      setFailure({ key: 'codeOutcomeUnknown' });
      return;
    }
    try {
      await signIn(token);
    } catch {
      setStep({ name: 'reload' });
      setFailure({ key: 'profileFailed' });
    }
  };

  const onSubmit = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>): void => {
    event.preventDefault();
    // The reload step has no submit button.
    if (pending || step.name === 'reload') return;
    setFailure(null);
    setPending(true);
    const submitted =
      step.name === 'password'
        ? submitPassword()
        : step.name === 'enroll'
          ? enroll(step.challengeToken)
          : submitCode(step.challengeToken);
    submitted.catch(fail).finally(() => {
      setPending(false);
    });
  };

  const startOver = (): void => {
    setStep({ name: 'password' });
    setCode('');
    setFailure(null);
  };

  return (
    <main className="login">
      {state.logoutFailed && (
        <div role="alert" className="card login-card login-alert">
          <p>{t('login.logoutFailed')}</p>
          <button type="button" className="button" onClick={() => void signOut()}>
            {t('login.retryLogout')}
          </button>
        </div>
      )}
      <form className="card login-card" noValidate onSubmit={onSubmit}>
        <div className="brand">{t('app.name')}</div>
        <h1>
          {t(
            step.name === 'enroll' || (step.name === 'code' && step.enrollment)
              ? 'login.enrollTitle'
              : 'login.title',
          )}
        </h1>
        {step.name === 'password' && (
          <>
            <label className="stacked-label">
              {t('login.email')}
              <input
                className="field"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value);
                }}
              />
            </label>
            <label className="stacked-label">
              {t('login.password')}
              <input
                className="field"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
              />
            </label>
          </>
        )}
        {step.name === 'code' && (
          <>
            {step.enrollment && <TotpEnrollmentHint enrollment={step.enrollment} />}
            <label className="stacked-label">
              {t('login.totp')}
              <input
                ref={codeInput}
                className="field"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(event) => {
                  // Apps show the code as "123 456"; a paste keeps the digits only.
                  setCode(event.target.value.replace(/\D/g, '').slice(0, totpCodeLength));
                }}
              />
            </label>
          </>
        )}
        {step.name === 'reload' ? (
          <button
            type="button"
            className="button button-primary"
            onClick={() => {
              window.location.reload();
            }}
          >
            {t('login.reload')}
          </button>
        ) : (
          <button type="submit" className="button button-primary" disabled={pending}>
            {t(submitLabel[step.name])}
          </button>
        )}
        {(step.name === 'enroll' || step.name === 'code') && (
          // Disabled while a step is in flight: its answer would land on the restarted form.
          <button type="button" className="button" disabled={pending} onClick={startOver}>
            {t('login.startOver')}
          </button>
        )}
        {failure ? (
          <p role="alert" className="login-error">
            {t(`login.errors.${failure.key}`, { minutes: failure.minutes })}
          </p>
        ) : (
          <p role="status" className="login-status">
            {pending ? t('login.pending') : ''}
          </p>
        )}
      </form>
    </main>
  );
}

function TotpEnrollmentHint({ enrollment }: { enrollment: TotpEnrollment }) {
  const { t } = useTranslation();
  const [qrSource, setQrSource] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    // Drawn here, never by an online QR service: the URI carries the 2FA secret.
    // Loaded on demand: only the first sign-in of a staff member needs it.
    import('qrcode')
      .then((qrcode) =>
        qrcode.toString(enrollment.otpauth_uri, {
          type: 'svg',
          margin: 1,
          // Dark on light in both themes: authenticator apps do not read inverted codes reliably.
          color: { dark: lightPalette.ink, light: lightPalette.surface },
        }),
      )
      .then((svg) => {
        if (active) setQrSource(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`);
      })
      // Without the QR code the secret below is still there to type in by hand.
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [enrollment.otpauth_uri]);

  return (
    <div className="enrollment">
      <p>{t('login.enrollText')}</p>
      {qrSource && <img className="enrollment-qr" src={qrSource} alt={t('login.qrAlt')} />}
      <p className="muted">{t('login.secret')}</p>
      <code className="enrollment-secret">{groupSecret(enrollment.secret)}</code>
    </div>
  );
}
