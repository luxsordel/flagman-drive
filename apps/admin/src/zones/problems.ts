import { ApiError } from '../api/client';

/** Keys under `zones.errors` in the translations. */
export const zoneErrors = [
  'selfIntersecting',
  'tooManyVertices',
  'tooFewVertices',
  'outsideArea',
  'notClosed',
  'holes',
  'invalid',
  'notFound',
  'disabled',
  'forbidden',
  'failed',
] as const;

export type ZoneError = (typeof zoneErrors)[number];

// The polygon rules of geo/domain.py, each with its own Problem type so the admin learns what to fix.
const polygonProblems: Readonly<Record<string, ZoneError>> = {
  'zone-polygon-self-intersecting': 'selfIntersecting',
  'zone-polygon-too-many-vertices': 'tooManyVertices',
  'zone-polygon-too-few-vertices': 'tooFewVertices',
  'zone-polygon-outside-area': 'outsideArea',
  'zone-polygon-not-closed': 'notClosed',
  'zone-polygon-holes': 'holes',
};

/**
 * What to tell the admin about a failed change of a zone. `signOut` is a 401: the session refreshes
 * the token ahead of expiry, so it means the session is over.
 */
export function zoneError(error: unknown): ZoneError | 'signOut' {
  if (!(error instanceof ApiError)) return 'failed';
  if (error.status === 401) return 'signOut';
  if (error.status === 403) return 'forbidden';
  const polygonProblem = error.problem ? polygonProblems[error.problem] : undefined;
  if (polygonProblem) return polygonProblem;
  if (error.problem === 'zone-not-found') return 'notFound';
  if (error.problem === 'zone-disabled') return 'disabled';
  if (error.status === 422) return 'invalid';
  return 'failed';
}
