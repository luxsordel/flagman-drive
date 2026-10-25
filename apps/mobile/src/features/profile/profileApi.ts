import type { components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import { profileErrorFromResponse, type ProfileError } from './profileErrors';

export type Profile = components['schemas']['ClientProfile'];

/** Fields `PATCH /me` accepts; anything left out stays as it is. */
export type ProfileUpdate = components['schemas']['ClientProfileUpdate'];

export type ProfileResult = { ok: true; profile: Profile } | { ok: false; error: ProfileError };

/** Failures come back as `{ ok: false }`, like KycApi; the store still guards against a throw. */
export interface ProfileApi {
  getProfile(): Promise<ProfileResult>;
  updateProfile(update: ProfileUpdate): Promise<ProfileResult>;
}

type Sent = Promise<{ data?: Profile; error?: unknown; response: Response }>;

/** `api` must already send the access token. */
export function createProfileApi(api: ApiClient): ProfileApi {
  return {
    getProfile: () => send(api.GET('/me')),
    updateProfile: (update) => send(api.PATCH('/me', { body: update })),
  };
}

async function send(request: Sent): Promise<ProfileResult> {
  try {
    const { data, error } = await request;
    if (data !== undefined) return { ok: true, profile: data };
    return { ok: false, error: profileErrorFromResponse(error) };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
