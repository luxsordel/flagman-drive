import { ApiError } from './client';

/** Why a page could not load: a retry helps only with `failed`. */
export type LoadProblem = 'forbidden' | 'notFound' | 'rejected' | 'failed';

export function loadProblem(error: unknown): LoadProblem {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'forbidden';
    if (error.status === 404) return 'notFound';
    if (error.status === 422) return 'rejected';
  }
  return 'failed';
}
