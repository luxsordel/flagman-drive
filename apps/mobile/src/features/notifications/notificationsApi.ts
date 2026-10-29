import type { components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import { notificationsErrorFromResponse, type NotificationsError } from './notificationsErrors';

export type NotificationPreferences = components['schemas']['NotificationPreferencesOut'];
export type NotificationCategory = components['schemas']['NotificationCategory'];
export type PushPlatform = components['schemas']['PushPlatform'];

export type Result<T> = { ok: true; value: T } | { ok: false; error: NotificationsError };

/** Failures come back as `{ ok: false }`, like PaymentsApi: the screen explains each one. */
export interface NotificationsApi {
  /** Pushes go to this token while the current session lasts; sign-out stops them on the server. */
  registerDevice(token: string, platform: PushPlatform): Promise<Result<null>>;
  /** Stops pushes to this phone; idempotent. */
  forgetDevice(): Promise<Result<null>>;
  getPreferences(): Promise<Result<NotificationPreferences>>;
  /** Only the categories to change; the others stay as they are. */
  updatePreferences(
    changes: Partial<Record<NotificationCategory, boolean>>,
  ): Promise<Result<NotificationPreferences>>;
}

interface Sent<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** `api` must already send the access token. */
export function createNotificationsApi(api: ApiClient): NotificationsApi {
  return {
    registerDevice: async (token, platform) => {
      try {
        const { response, error } = await api.PUT('/me/push-device', {
          body: { token, platform },
        });
        return response.ok
          ? { ok: true, value: null }
          : { ok: false, error: notificationsErrorFromResponse(response, error) };
      } catch {
        return { ok: false, error: { kind: 'network' } };
      }
    },
    forgetDevice: async () => {
      try {
        const { response, error } = await api.DELETE('/me/push-device');
        return response.ok
          ? { ok: true, value: null }
          : { ok: false, error: notificationsErrorFromResponse(response, error) };
      } catch {
        return { ok: false, error: { kind: 'network' } };
      }
    },
    getPreferences: () => send(api.GET('/me/notification-preferences')),
    updatePreferences: (changes) =>
      send(api.PATCH('/me/notification-preferences', { body: { categories: changes } })),
  };
}

async function send<T>(request: Promise<Sent<T>>): Promise<Result<T>> {
  try {
    const { data, error, response } = await request;
    if (data !== undefined) return { ok: true, value: data };
    return { ok: false, error: notificationsErrorFromResponse(response, error) };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
