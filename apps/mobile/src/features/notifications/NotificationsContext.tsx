import { createContext, useContext, type ReactNode } from 'react';

import type { AuthSession } from '../auth/session/authSession';
import type { NotificationsApi } from './notificationsApi';
import type { PushDeps } from './pushRegistration';

export interface NotificationsDeps {
  api: NotificationsApi;
  push: PushDeps;
  /** The app's page in the system settings, where a refused permission can be given after all. */
  openSystemSettings: () => void;
}

/** Props of PushListeners, shared by its native and web versions. */
export interface PushListenersProps {
  session: AuthSession;
  deps: NotificationsDeps;
}

const NotificationsContext = createContext<NotificationsDeps | null>(null);

export function NotificationsProvider({
  deps,
  children,
}: {
  deps: NotificationsDeps;
  children: ReactNode;
}) {
  return <NotificationsContext value={deps}>{children}</NotificationsContext>;
}

export function useNotifications(): NotificationsDeps {
  const deps = useContext(NotificationsContext);
  if (deps === null) throw new Error('useNotifications must be used inside NotificationsProvider');
  return deps;
}
