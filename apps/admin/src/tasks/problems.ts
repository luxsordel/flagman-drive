import { ApiError } from '../api/client';

/** Keys under `tasks.errors` in the translations. */
export type TaskErrorKey =
  | 'notFound'
  | 'changed'
  | 'duplicate'
  | 'carBusy'
  | 'chargeTooLow'
  | 'notInProgress'
  | 'assigneeInvalid'
  | 'tooFar'
  | 'locationUnknown'
  | 'noLocation'
  | 'rejected'
  | 'unavailable'
  | 'timeout'
  | 'rateLimited'
  | 'photoLimit'
  | 'photoInvalid'
  | 'storageUnavailable'
  | 'forbidden'
  | 'invalid'
  | 'failed';

export interface TaskError {
  key: TaskErrorKey;
  /** The car's reason code of a refusal, e.g. `ignition_on`. */
  reason?: string;
  retryAfterSeconds?: number;
}

const byProblem: Readonly<Record<string, TaskErrorKey>> = {
  'maintenance-task-not-found': 'notFound',
  'maintenance-task-photo-not-found': 'notFound',
  'car-not-found': 'notFound',
  'maintenance-task-transition-not-allowed': 'changed',
  'maintenance-task-duplicate': 'duplicate',
  'maintenance-car-busy': 'carBusy',
  'maintenance-charge-too-low': 'chargeTooLow',
  'maintenance-task-not-in-progress': 'notInProgress',
  'maintenance-assignee-invalid': 'assigneeInvalid',
  'too-far-from-car': 'tooFar',
  'car-location-unknown': 'locationUnknown',
  'car-command-rejected': 'rejected',
  'car-command-duplicate': 'timeout',
  'telematics-unavailable': 'unavailable',
  'telematics-timeout': 'timeout',
  'rate-limited': 'rateLimited',
  'maintenance-task-photo-limit': 'photoLimit',
  'maintenance-task-photo-too-large': 'photoInvalid',
  'maintenance-task-photo-type-unsupported': 'photoInvalid',
  'photo-storage-unavailable': 'storageUnavailable',
};

/**
 * What to tell the staff member about a refused action. `signOut` is a 401: the session renews the
 * token ahead of expiry, so it means the session is over.
 */
export function taskError(error: unknown): TaskError | 'signOut' {
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
  if (error.unavailable && error.status === 504) return { key: 'timeout' };
  if (error.unavailable) return { key: 'unavailable' };
  return { key: 'failed' };
}
