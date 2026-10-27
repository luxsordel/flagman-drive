import { createApiClient } from '@flagman/api-client';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import type { NotificationsDeps } from './NotificationsContext';
import { createNotificationsApi } from './notificationsApi';

const unsupported = () => Promise.reject(new Error('The web gets no push notifications'));

/** Settings only: browsers get no pushes, and expo-notifications is not loaded (ADR-0020). */
export function createAppNotifications(session: AuthSession): NotificationsDeps {
  const api = createNotificationsApi(
    createApiClient({ baseUrl: appApiUrl(), getAccessToken: session.getAccessToken }),
  );
  return {
    api,
    push: {
      platform: null,
      api,
      getPermission: unsupported,
      requestPermission: unsupported,
      getToken: unsupported,
    },
    openSystemSettings: () => undefined,
  };
}
