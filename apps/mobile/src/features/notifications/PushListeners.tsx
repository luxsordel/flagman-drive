import * as Notifications from 'expo-notifications';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useSyncExternalStore } from 'react';

import type { PushListenersProps } from './NotificationsContext';
import { notificationRoute } from './notificationRoute';
import { registerToken, syncPush } from './pushRegistration';

// A push that comes while the app is open still shows: it may be the reservation running out.
Notifications.setNotificationHandler({
  handleNotification: () =>
    Promise.resolve({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
});

/**
 * Keeps the device registered while signed in and opens the screen of a tapped notification.
 * Renders nothing; lives inside the router so it can navigate.
 */
export function PushListeners({ session, deps }: PushListenersProps) {
  const router = useRouter();
  const status = useSyncExternalStore(session.subscribe, session.getStatus);
  const signedIn = status === 'signedIn';
  const response = Notifications.useLastNotificationResponse();
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (!signedIn || deps.push.platform === null) return;
    // Never prompts here: the client is asked at a moment that explains why (#110).
    void syncPush(deps.push);
    const subscription = Notifications.addPushTokenListener(() => {
      // The listener gets the device token; the API wants the Expo one, which follows from it.
      void deps.push
        .getToken()
        .then((token) => registerToken(deps.push, token))
        .catch(() => undefined);
    });
    return () => {
      subscription.remove();
    };
  }, [signedIn, deps]);

  useEffect(() => {
    if (!signedIn || !response) return;
    const id = response.notification.request.identifier;
    if (handled.current === id) return;
    handled.current = id;
    const route = notificationRoute(response.notification.request.content.data);
    if (route !== null) router.push(route);
  }, [signedIn, response, router]);

  return null;
}
