import type { Staff } from './staff';

export interface AccessToken {
  accessToken: string;
  expiresInSeconds: number;
}

/** A new access token, or `rejected` when the API answered 401 to the refresh cookie. */
export type RefreshResult = AccessToken | 'rejected';

/**
 * Staff auth endpoints (#32). The refresh token never reaches this code: it lives in an HttpOnly
 * cookie that the browser sends by itself. Network and server failures reject the promise.
 */
export interface StaffAuthApi {
  refresh(): Promise<RefreshResult>;
  me(accessToken: string): Promise<Staff>;
  logout(): Promise<void>;
}

export interface StaffSession {
  /** The current access token. Kept only in memory (docs/03-security.md), so a reload starts from `restore`. */
  accessToken(): string | null;
  /** Signs in again from the refresh cookie; null when there is no live session. */
  restore(): Promise<Staff | null>;
  /**
   * Starts using the token of a sign-in that has just passed 2FA. Rejects when the staff profile
   * cannot be loaded; the server session and its cookie stay, so a reload restores them.
   */
  signIn(token: AccessToken): Promise<Staff>;
  /** Ends the session on the server and forgets the access token even if that call fails. */
  signOut(): Promise<void>;
  /** Called when the session ends: sign-out or a refresh the API rejected. Returns an unsubscribe function. */
  onSignedOut(listener: () => void): () => void;
}

// Refresh this long before the access token expires, so requests do not race its expiry.
const refreshMarginMs = 60_000;
// A background refresh that failed on the network is retried while the old token is still valid.
const retryAfterFailureMs = 15_000;

export function createStaffSession(api: StaffAuthApi): StaffSession {
  let accessToken: string | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<boolean> | null = null;
  const listeners = new Set<() => void>();

  const forget = (): void => {
    clearTimeout(timer);
    timer = undefined;
    accessToken = null;
  };

  const endSession = (): void => {
    forget();
    for (const listener of listeners) listener();
  };

  const schedule = (delayMs: number): void => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      refresh().then(
        (ok) => {
          if (!ok) endSession();
        },
        () => {
          schedule(retryAfterFailureMs);
        },
      );
    }, delayMs);
  };

  const accept = (token: AccessToken): void => {
    accessToken = token.accessToken;
    schedule(Math.max(token.expiresInSeconds * 1000 - refreshMarginMs, 0));
  };

  const loadStaff = async (token: string): Promise<Staff> => {
    try {
      return await api.me(token);
    } catch (error) {
      forget();
      throw error;
    }
  };

  const refreshOnce = async (): Promise<boolean> => {
    // ADR-0009: tabs share one cookie, and a 401 can mean another tab rotated it a moment ago.
    // By the retry the browser already has the new cookie; a second 401 means the session is over.
    let result = await api.refresh();
    if (result === 'rejected') result = await api.refresh();
    if (result === 'rejected') {
      forget();
      return false;
    }
    accept(result);
    return true;
  };

  // Concurrent callers share one request: a second refresh with the same cookie would look like reuse.
  const refresh = (): Promise<boolean> => {
    inFlight ??= refreshOnce().finally(() => {
      inFlight = null;
    });
    return inFlight;
  };

  return {
    accessToken: () => accessToken,

    async restore() {
      if (!(await refresh()) || accessToken === null) return null;
      return loadStaff(accessToken);
    },

    signIn(token) {
      accept(token);
      return loadStaff(token.accessToken);
    },

    async signOut() {
      // A refresh still in flight would store a fresh token after the session ended and schedule
      // the next one; let it land first, so endSession below forgets that token too.
      clearTimeout(timer);
      await inFlight?.catch(() => false);
      try {
        await api.logout();
      } finally {
        endSession();
      }
    },

    onSignedOut(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
