import { parseRetryAfter, problemSlug } from '../../api/problems';

/** What went wrong with the documents, in the terms the screens explain to the user. */
export type KycErrorKind =
  | 'network'
  | 'sessionExpired'
  | 'tooLarge'
  | 'unsupported'
  | 'locked'
  | 'incomplete'
  | 'rateLimited'
  | 'storageUnavailable'
  | 'cameraDenied'
  | 'unknown';

export interface KycError {
  kind: KycErrorKind;
  /** Seconds until the request may be repeated; only for `rateLimited`. */
  retryAfter?: number;
}

const kindByProblem: Readonly<Record<string, KycErrorKind>> = {
  'access-token-invalid': 'sessionExpired',
  'document-too-large': 'tooLarge',
  'document-type-unsupported': 'unsupported',
  'kyc-locked': 'locked',
  'kyc-incomplete': 'incomplete',
  'rate-limited': 'rateLimited',
  'document-storage-unavailable': 'storageUnavailable',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function kycErrorFromResponse(
  body: unknown,
  status: number,
  retryAfterHeader: string | null,
): KycError {
  const slug = problemSlug(body);
  // A proxy in front of the API answers 413 itself, without Problem Details.
  const kind =
    slug === null ? (status === 413 ? 'tooLarge' : 'unknown') : (kindByProblem[slug] ?? 'unknown');
  if (kind !== 'rateLimited') return { kind };
  const retryAfter = parseRetryAfter(retryAfterHeader);
  return retryAfter === null ? { kind } : { kind, retryAfter };
}

/** Errors that may pass by themselves, so the same photo is worth sending again. */
export function isRetryable(error: KycError): boolean {
  return ['network', 'rateLimited', 'storageUnavailable', 'unknown'].includes(error.kind);
}
