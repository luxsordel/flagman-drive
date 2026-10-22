import type { createApiClient, TokenPair } from '@flagman/api-client';

import { authErrorFromResponse, type AuthError } from './authErrors';

export type ApiClient = ReturnType<typeof createApiClient>;

export interface TokenStore {
  /** `body`: the app keeps the refresh token itself; `cookie`: the browser does (ADR-0009). */
  readonly transport: 'body' | 'cookie';
  getRefreshToken(): Promise<string | null>;
  setRefreshToken(token: string | null): Promise<void>;
  /** Stable per installation: the server revokes sessions per device on refresh token reuse. */
  getDeviceId(): Promise<string>;
}

export type AuthStatus = 'restoring' | 'signedOut' | 'signedIn';

/** A code was sent to this phone; kept in memory only, so a phone never ends up in a URL. */
export interface PendingSignIn {
  phone: string;
  /** Epoch ms when the API accepts another code request for this phone. */
  resendAvailableAt: number;
}

export interface AuthSessionOptions {
  /** Client without an access token: the auth endpoints do not take one. */
  api: ApiClient;
  store: TokenStore;
  now?: () => number;
}

// Refresh ahead of expiry so a request does not leave with a token that dies in flight.
const refreshMarginMs = 60_000;

type Sent<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

/**
 * Client sign-in state: phone + OTP sign-in, access token in memory, refresh token rotation,
 * sign-out. Framework-free so the token rules are unit-tested without rendering anything.
 */
export class AuthSession {
  private readonly api: ApiClient;
  private readonly store: TokenStore;
  private readonly now: () => number;
  private readonly listeners = new Set<() => void>();
  private status: AuthStatus = 'restoring';
  private pending: PendingSignIn | null = null;
  private access: { token: string; expiresAt: number } | null = null;
  private refreshing: Promise<boolean> | null = null;
  // Bumped by forget(): a rotation that was in flight then must not store its tokens.
  private generation = 0;

  constructor({ api, store, now = Date.now }: AuthSessionOptions) {
    this.api = api;
    this.store = store;
    this.now = now;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getStatus = (): AuthStatus => this.status;

  readonly getPendingSignIn = (): PendingSignIn | null => this.pending;

  /** Resumes the previous session at launch; never throws, so the splash screen always hides. */
  async restore(): Promise<void> {
    try {
      this.setStatus((await this.refresh()) ? 'signedIn' : 'signedOut');
    } catch {
      // Offline at launch: a stored refresh token is still a session to resume once online.
      const stored = await this.store.getRefreshToken().catch(() => null);
      this.setStatus(stored === null ? 'signedOut' : 'signedIn');
    }
  }

  /** Sends a code by SMS; on success the code screen can use `getPendingSignIn()`. */
  async requestCode(phone: string): Promise<AuthError | null> {
    const result = await send(this.api.POST('/auth/otp/request', { body: { phone } }));
    if (!result.ok) return result.error;
    this.pending = { phone, resendAvailableAt: this.now() + result.data.resend_after * 1000 };
    this.emit();
    return null;
  }

  async verifyCode(code: string): Promise<AuthError | null> {
    const pending = this.pending;
    if (pending === null) return { kind: 'otpExpired' };
    let deviceId: string;
    try {
      deviceId = await this.store.getDeviceId();
    } catch {
      // Keychain/Keystore failure: reported like any other error so the screen stays usable.
      return { kind: 'unknown' };
    }
    const result = await send(
      this.api.POST('/auth/otp/verify', {
        body: {
          phone: pending.phone,
          code,
          device_id: deviceId,
          refresh_token_transport: this.store.transport,
        },
      }),
    );
    if (!result.ok) return result.error;
    await this.accept(result.data);
    // `pending` stays until sign-out: the code screen must not see it vanish before the
    // router has moved away on the status change.
    this.setStatus('signedIn');
    return null;
  }

  /**
   * Access token for API calls, refreshed when close to expiry; null once signed out.
   * Throws when the API cannot be reached, so the caller's request fails as a network error.
   */
  readonly getAccessToken = async (): Promise<string | null> => {
    if (this.status !== 'signedIn') return null;
    if (this.access !== null && this.access.expiresAt - this.now() > refreshMarginMs) {
      return this.access.token;
    }
    return (await this.refresh()) ? (this.access?.token ?? null) : null;
  };

  /** Never throws: the user is signed out locally whatever the network or the keychain does. */
  async signOut(): Promise<void> {
    // Logs out with the token a rotation in flight is about to store, not the one it replaces.
    await this.refreshing?.catch(() => undefined);
    const refreshToken = await this.store.getRefreshToken().catch(() => null);
    if (this.store.transport === 'cookie' || refreshToken !== null) {
      // Waits for the server on the web: only its response clears the HttpOnly cookie,
      // otherwise the next page load would sign the user back in.
      await this.api
        .POST('/auth/logout', {
          body: refreshToken === null ? {} : { refresh_token: refreshToken },
        })
        .catch(() => undefined);
    }
    // A keychain that refuses the delete still leaves the app signed out for this run.
    await this.forget().catch(() => undefined);
  }

  // Rotation is single-use on the server, so parallel callers must share one refresh call.
  private refresh(): Promise<boolean> {
    this.refreshing ??= this.rotate().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  private async rotate(): Promise<boolean> {
    // Two browser tabs share the cookie: when the other tab has just rotated it, the second
    // attempt goes out with the new cookie (ADR-0009). The app holds its token alone.
    const attempts = this.store.transport === 'cookie' ? 2 : 1;
    const generation = this.generation;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const refreshToken = await this.store.getRefreshToken();
      if (this.store.transport === 'body' && refreshToken === null) break;
      const { data, response } = await this.api.POST('/auth/token/refresh', {
        body: refreshToken === null ? {} : { refresh_token: refreshToken },
      });
      // Signed out meanwhile: storing the new refresh token would resume the session at the
      // next launch.
      if (generation !== this.generation) return false;
      if (data !== undefined) {
        await this.accept(data);
        return true;
      }
      // Only 401 means the session is gone; a 5xx must not throw away a valid refresh token.
      if (response.status !== 401)
        throw new Error(`Token refresh failed: HTTP ${String(response.status)}`);
    }
    await this.forget();
    return false;
  }

  private async accept(tokens: TokenPair): Promise<void> {
    this.access = { token: tokens.access_token, expiresAt: this.now() + tokens.expires_in * 1000 };
    if (tokens.refresh_token != null) await this.store.setRefreshToken(tokens.refresh_token);
  }

  private async forget(): Promise<void> {
    this.generation += 1;
    this.access = null;
    this.pending = null;
    try {
      await this.store.setRefreshToken(null);
    } finally {
      this.setStatus('signedOut');
    }
  }

  private setStatus(status: AuthStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.emit();
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }
}

type SendResult<T> = { ok: true; data: T } | { ok: false; error: AuthError };

async function send<T>(request: Sent<T>): Promise<SendResult<T>> {
  try {
    const { data, error, response } = await request;
    if (data !== undefined) return { ok: true, data };
    return { ok: false, error: authErrorFromResponse(error, response) };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
