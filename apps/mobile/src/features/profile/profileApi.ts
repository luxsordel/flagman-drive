import type { Language } from '../../i18n';
import type { ProfileError } from './profileErrors';

// Hand-written until the API serves `/me` (#33); then these become the generated schema types.
export interface Profile {
  first_name: string;
  last_name: string;
  /** ISO date, `YYYY-MM-DD`; null until the client fills it in. */
  birth_date: string | null;
  email: string | null;
  language: Language;
  /**
   * E.164, the number the client signs in with; not editable through the profile.
   * Empty only in the mock after an app restart, when the number is no longer known (#33).
   */
  phone: string;
}

/** Fields `PATCH /me` accepts; anything left out stays as it is. */
export type ProfileUpdate = Partial<Omit<Profile, 'phone'>>;

export type ProfileResult = { ok: true; profile: Profile } | { ok: false; error: ProfileError };

/** Failures come back as `{ ok: false }`, like KycApi; the store still guards against a throw. */
export interface ProfileApi {
  getProfile(): Promise<ProfileResult>;
  updateProfile(update: ProfileUpdate): Promise<ProfileResult>;
}

export interface MockProfileApiOptions {
  initial: Profile;
  /** Simulated round trip, so the screen shows its busy states as with the real API. */
  delayMs?: number;
}

/** In-memory stand-in for `/me` until #33 lands; nothing leaves the device. */
export function createMockProfileApi({
  initial,
  delayMs = 300,
}: MockProfileApiOptions): ProfileApi {
  let profile = initial;
  const reply = async (): Promise<ProfileResult> => {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    return { ok: true, profile };
  };
  return {
    getProfile: reply,
    updateProfile: (update) => {
      profile = { ...profile, ...update };
      return reply();
    },
  };
}
