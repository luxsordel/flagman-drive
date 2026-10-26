import { parseRetryAfter, problemSlug } from '@flagman/api-client';

/**
 * What went wrong with a payments request, in the terms the screen explains to the user.
 * `unavailable`: the server has no payment provider configured, nothing the client can fix.
 * `notFound`: the card was removed meanwhile, e.g. on another phone.
 */
export type PaymentsErrorKind =
  'network' | 'sessionExpired' | 'rateLimited' | 'unavailable' | 'notFound' | 'unknown';

export interface PaymentsError {
  kind: PaymentsErrorKind;
  /** With `rateLimited`: seconds until the next try. */
  retryAfterSeconds?: number;
}

const kindByProblem: Readonly<Record<string, PaymentsErrorKind>> = {
  'access-token-invalid': 'sessionExpired',
  'rate-limited': 'rateLimited',
  'payments-unavailable': 'unavailable',
  'payment-card-not-found': 'notFound',
  'card-binding-not-found': 'notFound',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function paymentsErrorFromResponse(response: Response, body: unknown): PaymentsError {
  const slug = problemSlug(body);
  const kind = slug === null ? 'unknown' : (kindByProblem[slug] ?? 'unknown');
  if (kind !== 'rateLimited') return { kind };
  const retryAfterSeconds = parseRetryAfter(response.headers.get('Retry-After'));
  return retryAfterSeconds === null ? { kind } : { kind, retryAfterSeconds };
}
