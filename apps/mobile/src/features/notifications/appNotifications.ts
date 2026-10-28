import { createApiClient } from '@flagman/api-client';
import * as Notifications from 'expo-notifications';
import i18next from 'i18next';
import { Linking, Platform } from 'react-native';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import type { NotificationsDeps } from './NotificationsContext';
import { createNotificationsApi, type NotificationsApi } from './notificationsApi';
import type { PushDeps } from './pushRegistration';

// The API sends to this channel (ANDROID_CHANNEL in apps/api/src/flagman/notifications/expo.py).
const androidChannel = 'default';

async function ensureAndroidChannel(): Promise<void> {
  // Android 13+ shows the permission prompt only once a channel exists.
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync(androidChannel, {
    name: i18next.t('notifications.androidChannel'),
    importance: Notifications.AndroidImportance.HIGH,
  });
}

function permission(response: Notifications.NotificationPermissionsStatus) {
  return { granted: response.granted, canAskAgain: response.canAskAgain };
}

export function createAppNotifications(
  session: AuthSession,
  api: NotificationsApi = createNotificationsApi(
    createApiClient({ baseUrl: appApiUrl(), getAccessToken: session.getAccessToken }),
  ),
): NotificationsDeps {
  const platform = Platform.OS === 'ios' || Platform.OS === 'android' ? Platform.OS : null;
  const push: PushDeps = {
    platform,
    api,
    getPermission: async () => permission(await Notifications.getPermissionsAsync()),
    requestPermission: async () => {
      await ensureAndroidChannel();
      return permission(await Notifications.requestPermissionsAsync());
    },
    getToken: async () => {
      await ensureAndroidChannel();
      // The EAS project id comes from app.config.ts (EAS_PROJECT_ID); without it this rejects.
      return (await Notifications.getExpoPushTokenAsync()).data;
    },
  };
  // The server stops pushes to a signed-out session anyway; this also deletes the token at once.
  session.beforeSignOut(() => api.forgetDevice());
  return {
    api,
    push,
    openSystemSettings: () => {
      void Linking.openSettings();
    },
  };
}
