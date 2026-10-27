import type { NotificationsApi, PushPlatform } from './notificationsApi';

export interface PushPermission {
  granted: boolean;
  /** False once the user said no for good: only the system settings can turn pushes on. */
  canAskAgain: boolean;
}

/** What the app needs from the OS and Expo; expo-notifications in the app, fakes in the tests. */
export interface PushDeps {
  /** Null on the web, which gets no pushes. */
  platform: PushPlatform | null;
  api: Pick<NotificationsApi, 'registerDevice'>;
  getPermission(): Promise<PushPermission>;
  /** Shows the system prompt. */
  requestPermission(): Promise<PushPermission>;
  /** Rejects on a simulator, without an EAS project id, or offline. */
  getToken(): Promise<string>;
}

/**
 * `unsupported`: the web. `askable`: never asked, or asked and the OS allows asking again.
 * `blocked`: refused; only the system settings can change it. `enabled`: registered with the API.
 * `failed`: allowed, but the token could not be had or registered; trying again may help.
 */
export type PushState = 'unsupported' | 'askable' | 'blocked' | 'enabled' | 'failed';

/** Registers the device if the client already allowed pushes; never shows the system prompt. */
export async function syncPush(deps: PushDeps): Promise<PushState> {
  if (deps.platform === null) return 'unsupported';
  return settle(deps, await deps.getPermission().catch(() => null));
}

/** Asks for the permission when the OS still allows it, then registers the device. */
export async function enablePush(deps: PushDeps): Promise<PushState> {
  if (deps.platform === null) return 'unsupported';
  const current = await deps.getPermission().catch(() => null);
  if (current === null || current.granted || !current.canAskAgain) return settle(deps, current);
  return settle(deps, await deps.requestPermission().catch(() => null));
}

/** For a token the OS replaced while the app runs. */
export async function registerToken(deps: PushDeps, token: string): Promise<PushState> {
  if (deps.platform === null) return 'unsupported';
  const result = await deps.api.registerDevice(token, deps.platform);
  return result.ok ? 'enabled' : 'failed';
}

async function settle(deps: PushDeps, permission: PushPermission | null): Promise<PushState> {
  if (permission === null) return 'failed';
  if (!permission.granted) return permission.canAskAgain ? 'askable' : 'blocked';
  let token: string;
  try {
    token = await deps.getToken();
  } catch {
    return 'failed';
  }
  return registerToken(deps, token);
}
