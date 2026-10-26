import { ApiError } from '../api/client';

/** Keys under `fleet.commandErrors` in the translations. */
export const commandErrors = [
  'activeRental',
  'rejected',
  'duplicate',
  'unavailable',
  'timeout',
  'rateLimited',
  'invalid',
  'notFound',
  'forbidden',
  'failed',
] as const;

export type CommandErrorKey = (typeof commandErrors)[number];

export interface CommandError {
  key: CommandErrorKey;
  /** The car's reason code of a refusal, e.g. `ignition_on`. */
  reason?: string;
  retryAfterSeconds?: number;
}

const byProblem: Readonly<Record<string, CommandErrorKey>> = {
  'car-in-active-rental': 'activeRental',
  'car-command-rejected': 'rejected',
  'car-command-duplicate': 'duplicate',
  'telematics-unavailable': 'unavailable',
  'telematics-timeout': 'timeout',
  'rate-limited': 'rateLimited',
  'car-not-found': 'notFound',
};

/**
 * What to tell the staff member about a failed command. `signOut` is a 401: the session renews the
 * token ahead of expiry, so it means the session is over.
 */
export function commandError(error: unknown): CommandError | 'signOut' {
  if (!(error instanceof ApiError)) return { key: 'failed' };
  if (error.status === 401) return 'signOut';
  if (error.status === 403) return { key: 'forbidden' };
  const key = error.problem ? byProblem[error.problem] : undefined;
  if (key === 'rejected') return { key, reason: error.detail ?? 'unknown' };
  if (key === 'rateLimited' && error.retryAfterSeconds !== undefined) {
    return { key, retryAfterSeconds: error.retryAfterSeconds };
  }
  if (key) return { key };
  if (error.status === 422) return { key: 'invalid' };
  // A proxy that gave up waiting: the API may still have reached the car.
  if (error.unavailable && error.status === 504) return { key: 'timeout' };
  // A proxy in front of a stopped API: nothing reached the car.
  if (error.unavailable) return { key: 'unavailable' };
  return { key: 'failed' };
}
