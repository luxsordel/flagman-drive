import type { Profile } from '../profile/profileApi';
import type { DocumentKind, KycApplication, KycStatus } from './kycApi';

/** The order the client photographs the documents in; the API does not impose one. */
export const documentSteps: readonly DocumentKind[] = [
  'license_front',
  'license_back',
  'passport',
  'selfie_with_license',
];

/** Documents of an application under review or approved stay as they were checked (ADR-0012). */
export function canEditDocuments(status: KycStatus): boolean {
  return status === 'not_submitted' || status === 'rejected';
}

/**
 * The operator checks the documents against the name and birth date, and they lock once the
 * documents are sent: an application with an empty profile could be neither approved nor fixed.
 */
export function hasProfileForKyc(profile: Profile): boolean {
  return Boolean(profile.first_name && profile.last_name && profile.birth_date);
}

export function isUploaded(application: KycApplication, kind: DocumentKind): boolean {
  return application.documents.some((document) => document.kind === kind);
}

/** The first document still to upload, or `review` when all are there and can be sent. */
export function firstStep(application: KycApplication): DocumentKind | 'review' {
  return documentSteps.find((kind) => application.missing_documents.includes(kind)) ?? 'review';
}

/**
 * Where to go after `current` was uploaded: the next missing document, then one skipped earlier,
 * then the overview. Replacing a photo when nothing is missing goes straight back to the overview.
 */
export function stepAfter(
  application: KycApplication,
  current: DocumentKind,
): DocumentKind | 'review' {
  const rest = documentSteps.slice(documentSteps.indexOf(current) + 1);
  return (
    rest.find((kind) => application.missing_documents.includes(kind)) ?? firstStep(application)
  );
}
