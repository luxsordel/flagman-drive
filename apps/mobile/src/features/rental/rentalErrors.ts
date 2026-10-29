import { parseRetryAfter, problemSlug } from '@flagman/api-client';

/** What went wrong with a rental, in the terms the screens explain to the user. */
export type RentalErrorKind =
  | 'network'
  | 'sessionExpired'
  | 'rateLimited'
  | 'kycNotApproved'
  | 'blocked'
  | 'carGone'
  | 'activeRental'
  | 'expired'
  | 'notAllowed'
  | 'busy'
  | 'inspectionRequired'
  | 'inspectionClosed'
  | 'inspectionIncomplete'
  | 'photoLimit'
  | 'damageLimit'
  | 'tooLarge'
  | 'unsupported'
  | 'storageUnavailable'
  | 'cameraDenied'
  | 'locationDenied'
  | 'locationUnavailable'
  | 'tooFar'
  | 'carLocationUnknown'
  | 'carRefused'
  | 'carUnreachable'
  | 'carTimeout'
  | 'notFound'
  | 'unknown';

export interface RentalError {
  kind: RentalErrorKind;
  /** Seconds until the request may be repeated; only for `rateLimited`. */
  retryAfter?: number;
}

const kindByProblem: Readonly<Record<string, RentalErrorKind>> = {
  'access-token-invalid': 'sessionExpired',
  'rate-limited': 'rateLimited',
  'kyc-not-approved': 'kycNotApproved',
  'client-blocked': 'blocked',
  // Reserved by someone else or out of charge since the map was loaded: the API does not say.
  'car-not-found': 'carGone',
  'active-rental-exists': 'activeRental',
  'reservation-expired': 'expired',
  'rental-transition-not-allowed': 'notAllowed',
  'rental-command-in-progress': 'busy',
  'rental-not-found': 'notFound',
  'inspection-required': 'inspectionRequired',
  'inspection-closed': 'inspectionClosed',
  'inspection-incomplete': 'inspectionIncomplete',
  'inspection-photo-limit': 'photoLimit',
  'inspection-damage-limit': 'damageLimit',
  'inspection-photo-too-large': 'tooLarge',
  'inspection-photo-type-unsupported': 'unsupported',
  'photo-storage-unavailable': 'storageUnavailable',
  'too-far-from-car': 'tooFar',
  'car-location-unknown': 'carLocationUnknown',
  'car-command-rejected': 'carRefused',
  'telematics-unavailable': 'carUnreachable',
  'telematics-timeout': 'carTimeout',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function rentalErrorFromResponse(
  body: unknown,
  status: number,
  retryAfterHeader: string | null,
): RentalError {
  const slug = problemSlug(body);
  // A proxy in front of the API answers 413 itself, without Problem Details.
  const kind =
    slug === null ? (status === 413 ? 'tooLarge' : 'unknown') : (kindByProblem[slug] ?? 'unknown');
  if (kind !== 'rateLimited') return { kind };
  const retryAfter = parseRetryAfter(retryAfterHeader);
  return retryAfter === null ? { kind } : { kind, retryAfter };
}

/** Errors that may pass by themselves, so the same request is worth sending again. */
export function isRetryable(error: RentalError): boolean {
  return [
    'network',
    'rateLimited',
    'storageUnavailable',
    'busy',
    'locationUnavailable',
    'carLocationUnknown',
    'carUnreachable',
    'unknown',
  ].includes(error.kind);
}

/** The rental changed on the server (expired, cancelled, started): the screen reloads it. */
export function isStale(error: RentalError): boolean {
  return ['expired', 'notAllowed', 'inspectionClosed', 'carTimeout'].includes(error.kind);
}
