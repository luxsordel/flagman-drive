import { parseRetryAfter, problemSlug } from '@flagman/api-client';

/** What went wrong at sign-in, in the terms the screens explain to the user. */
export type AuthErrorKind =
  | 'otpInvalid'
  | 'otpExpired'
  | 'rateLimited'
  | 'signInUnavailable'
  | 'signInNotAllowed'
  | 'network'
  | 'unknown';

export interface AuthError {
  kind: AuthErrorKind;
  /** Seconds until the request may be repeated; only for `rateLimited`. */
  retryAfter?: number;
}

const kindByProblem: Readonly<Record<string, AuthErrorKind>> = {
  'otp-invalid': 'otpInvalid',
  'otp-expired': 'otpExpired',
  'rate-limited': 'rateLimited',
  'sign-in-unavailable': 'signInUnavailable',
  'sign-in-not-allowed': 'signInNotAllowed',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function authErrorFromResponse(body: unknown, response: Response): AuthError {
  const slug = problemSlug(body);
  const kind = slug === null ? 'unknown' : (kindByProblem[slug] ?? 'unknown');
  if (kind !== 'rateLimited') return { kind };
  const retryAfter = parseRetryAfter(response.headers.get('Retry-After'));
  return retryAfter === null ? { kind } : { kind, retryAfter };
}
