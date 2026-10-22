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

const problemPrefix = 'urn:flagman:problem:';

const kindByProblem: Readonly<Record<string, AuthErrorKind>> = {
  'otp-invalid': 'otpInvalid',
  'otp-expired': 'otpExpired',
  'rate-limited': 'rateLimited',
  'sign-in-unavailable': 'signInUnavailable',
  'sign-in-not-allowed': 'signInNotAllowed',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function authErrorFromResponse(body: unknown, response: Response): AuthError {
  const type = problemType(body);
  const kind = type?.startsWith(problemPrefix)
    ? (kindByProblem[type.slice(problemPrefix.length)] ?? 'unknown')
    : 'unknown';
  if (kind !== 'rateLimited') return { kind };
  const retryAfter = parseRetryAfter(response.headers.get('Retry-After'));
  return retryAfter === null ? { kind } : { kind, retryAfter };
}

// A proxy or an outage can answer with HTML or an empty body instead of Problem Details.
function problemType(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('type' in body)) return null;
  return typeof body.type === 'string' ? body.type : null;
}

// The API sends delta-seconds; an HTTP date or garbage is ignored rather than guessed at.
function parseRetryAfter(value: string | null): number | null {
  const trimmed = value?.trim() ?? '';
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null;
}
