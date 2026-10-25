import type { Profile, ProfileApi, ProfileResult, ProfileUpdate } from './profileApi';
import { ProfileStore } from './profileStore';

/** Test doubles shared by the profile tests. */

export function profile(overrides: Partial<Profile> = {}): Profile {
  return {
    first_name: 'Ivan',
    last_name: 'Petrov',
    birth_date: '1995-03-15',
    email: null,
    language: 'ru',
    phone: '+375291234567',
    ...overrides,
  };
}

export const ok = (value: Profile): ProfileResult => ({ ok: true, profile: value });

export function fakeProfileApi() {
  return {
    getProfile: jest.fn<Promise<ProfileResult>, []>(),
    updateProfile: jest.fn<Promise<ProfileResult>, [ProfileUpdate]>(),
  } satisfies ProfileApi;
}

/** A store that loads `value`, for screens outside the profile that read it. */
export function profileStoreWith(value: Profile) {
  const api = fakeProfileApi();
  api.getProfile.mockResolvedValue(ok(value));
  return { api, store: new ProfileStore(api) };
}
