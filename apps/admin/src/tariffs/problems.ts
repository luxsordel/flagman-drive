import { ApiError } from '../api/client';

/** Keys under `tariffs.errors` in the translations. */
export const tariffErrors = [
  'changed',
  'exists',
  'notFound',
  'modelNotFound',
  'invalid',
  'forbidden',
  'failed',
] as const;

export type TariffError = (typeof tariffErrors)[number];

/** The list on the page is out of date: these errors offer to load it again. */
export const staleErrors: ReadonlySet<TariffError> = new Set([
  'changed',
  'exists',
  'notFound',
  'modelNotFound',
]);

const problems: Readonly<Record<string, TariffError>> = {
  'tariff-changed': 'changed',
  'tariff-exists': 'exists',
  'tariff-not-found': 'notFound',
  'car-model-not-found': 'modelNotFound',
};

/**
 * What to tell the admin about a failed save of a tariff. `signOut` is a 401: the session refreshes
 * the token ahead of expiry, so it means the session is over.
 */
export function tariffError(error: unknown): TariffError | 'signOut' {
  if (!(error instanceof ApiError)) return 'failed';
  if (error.status === 401) return 'signOut';
  if (error.status === 403) return 'forbidden';
  const known = error.problem ? problems[error.problem] : undefined;
  if (known) return known;
  if (error.status === 422) return 'invalid';
  return 'failed';
}
