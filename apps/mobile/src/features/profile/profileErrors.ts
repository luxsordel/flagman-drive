/**
 * What went wrong with the profile request, in the terms the screen explains to the user.
 * The mock never fails; `sessionExpired` and `invalid` come with the Problem Details mapping
 * once the API serves `/me` (#33).
 */
export type ProfileErrorKind = 'network' | 'sessionExpired' | 'invalid' | 'unknown';

export interface ProfileError {
  kind: ProfileErrorKind;
}
