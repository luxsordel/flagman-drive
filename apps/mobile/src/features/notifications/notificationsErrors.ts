import { parseRetryAfter, problemSlug } from '@flagman/api-client';

/**
 * What went wrong with a notifications request, in the terms the screen explains to the user.
 * `mandatory`: trip and payment notifications cannot be switched off; the server refuses it too.
 */
export type NotificationsErrorKind =
  'network' | 'sessionExpired' | 'rateLimited' | 'mandatory' | 'unknown';

export interface NotificationsError {
  kind: NotificationsErrorKind;
  /** With `rateLimited`: seconds until the next try. */
  retryAfterSeconds?: number;
}

const kindByProblem: Readonly<Record<string, NotificationsErrorKind>> = {
  'access-token-invalid': 'sessionExpired',
  'rate-limited': 'rateLimited',
  'notification-category-mandatory': 'mandatory',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function notificationsErrorFromResponse(
  response: Response,
  body: unknown,
): NotificationsError {
  const slug = problemSlug(body);
  const kind = slug === null ? 'unknown' : (kindByProblem[slug] ?? 'unknown');
  if (kind !== 'rateLimited') return { kind };
  const retryAfterSeconds = parseRetryAfter(response.headers.get('Retry-After'));
  return retryAfterSeconds === null ? { kind } : { kind, retryAfterSeconds };
}
