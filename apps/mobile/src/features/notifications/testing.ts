import type { NotificationsDeps } from './NotificationsContext';
import type {
  NotificationCategory,
  NotificationPreferences,
  NotificationsApi,
  PushPlatform,
  Result,
} from './notificationsApi';
import type { PushDeps, PushPermission } from './pushRegistration';

/** Test doubles shared by the notifications tests. */

export const ok = <T>(value: T): Result<T> => ({ ok: true, value });

export function preferences(disabled: NotificationCategory[] = []): NotificationPreferences {
  const mandatory: NotificationCategory[] = ['trip', 'payment'];
  return {
    categories: (['reservation', 'trip', 'payment'] as const).map((category) => ({
      category,
      enabled: !disabled.includes(category),
      mandatory: mandatory.includes(category),
    })),
  };
}

export function fakeNotificationsApi() {
  return {
    registerDevice: jest.fn<Promise<Result<null>>, [string, PushPlatform]>(() =>
      Promise.resolve(ok(null)),
    ),
    forgetDevice: jest.fn<Promise<Result<null>>, []>(() => Promise.resolve(ok(null))),
    getPreferences: jest.fn<Promise<Result<NotificationPreferences>>, []>(),
    updatePreferences: jest.fn<
      Promise<Result<NotificationPreferences>>,
      [Partial<Record<NotificationCategory, boolean>>]
    >(),
  } satisfies NotificationsApi;
}

export const granted: PushPermission = { granted: true, canAskAgain: true };
export const notAsked: PushPermission = { granted: false, canAskAgain: true };
export const refused: PushPermission = { granted: false, canAskAgain: false };

export function fakePushDeps(
  permission: PushPermission = granted,
  platform: PushPlatform | null = 'ios',
) {
  return {
    platform,
    api: fakeNotificationsApi(),
    getPermission: jest.fn<Promise<PushPermission>, []>(() => Promise.resolve(permission)),
    requestPermission: jest.fn<Promise<PushPermission>, []>(() => Promise.resolve(granted)),
    getToken: jest.fn<Promise<string>, []>(() => Promise.resolve('ExponentPushToken[abc]')),
  } satisfies PushDeps;
}

export function fakeNotificationsDeps(push = fakePushDeps()) {
  return {
    api: push.api,
    push,
    openSystemSettings: jest.fn<undefined, []>(),
  } satisfies NotificationsDeps;
}
