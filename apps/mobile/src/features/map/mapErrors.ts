import { problemSlug } from '@flagman/api-client';

/** What went wrong with a map request, in the terms the screen explains to the user. */
export type MapErrorKind = 'network' | 'sessionExpired' | 'rateLimited' | 'carGone' | 'unknown';

export interface MapError {
  kind: MapErrorKind;
}

const kindByProblem: Readonly<Record<string, MapErrorKind>> = {
  'access-token-invalid': 'sessionExpired',
  'rate-limited': 'rateLimited',
  // Rented, reserved or out of charge since the map was loaded: the API does not say which.
  'car-not-found': 'carGone',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function mapErrorFromResponse(body: unknown): MapError {
  const slug = problemSlug(body);
  return { kind: slug === null ? 'unknown' : (kindByProblem[slug] ?? 'unknown') };
}
