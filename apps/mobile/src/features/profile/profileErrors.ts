import { problemSlug } from '@flagman/api-client';

/**
 * What went wrong with the profile request, in the terms the screen explains to the user.
 * `birthDateInvalid` is shown at the date field: the server's clock may disagree with the phone's.
 * `locked`: the documents went for review after the screen opened, so the name and birth date
 * no longer change.
 */
export type ProfileErrorKind =
  'network' | 'sessionExpired' | 'invalid' | 'birthDateInvalid' | 'locked' | 'unknown';

export interface ProfileError {
  kind: ProfileErrorKind;
}

const kindByProblem: Readonly<Record<string, ProfileErrorKind>> = {
  'access-token-invalid': 'sessionExpired',
  'validation-error': 'invalid',
  'birth-date-invalid': 'birthDateInvalid',
  'kyc-locked': 'locked',
};

/** Maps an error response with a Problem Details body (ADR-0006) to an error the UI can explain. */
export function profileErrorFromResponse(body: unknown): ProfileError {
  const slug = problemSlug(body);
  return { kind: slug === null ? 'unknown' : (kindByProblem[slug] ?? 'unknown') };
}
