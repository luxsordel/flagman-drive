import { createApiClient } from '@flagman/api-client';
import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { Platform } from 'react-native';

import { AuthSession, type AuthStatus, type PendingSignIn } from './authSession';
import { createTokenStore } from './tokenStore';

const defaultApiUrl = 'http://localhost:8000';

/** The deployed web stand serves the API on the same origin through Caddy. */
export function webApiUrl(location: Pick<Location, 'origin'>): string {
  return location.origin;
}

/** An empty value, as in a copied `.env.example`, means "not set" rather than the app's own origin. */
export function resolveApiUrl(configured: string | undefined): string {
  const trimmed = configured?.trim() ?? '';
  return trimmed === '' ? defaultApiUrl : trimmed;
}

/** API origin of this build. */
export function appApiUrl(): string {
  const configured = process.env.EXPO_PUBLIC_API_URL;
  // A configured URL is useful for local web development and native builds.
  if (configured?.trim()) return resolveApiUrl(configured);
  // The deployed web stand serves API paths on the same origin through Caddy.
  if (Platform.OS === 'web' && typeof window !== 'undefined') return webApiUrl(window.location);
  return resolveApiUrl(configured);
}

export function createAppAuthSession(): AuthSession {
  const api = createApiClient({
    baseUrl: appApiUrl(),
    // The web app runs on another origin than the API; the refresh cookie must ride along.
    ...(Platform.OS === 'web' ? { credentials: 'include' as const } : {}),
  });
  return new AuthSession({ api, store: createTokenStore() });
}

const AuthContext = createContext<AuthSession | null>(null);

export function AuthProvider({ session, children }: { session: AuthSession; children: ReactNode }) {
  return <AuthContext value={session}>{children}</AuthContext>;
}

export function useAuthSession(): AuthSession {
  const session = useContext(AuthContext);
  if (session === null) throw new Error('useAuthSession must be used inside AuthProvider');
  return session;
}

export function useAuthStatus(): AuthStatus {
  const session = useAuthSession();
  return useSyncExternalStore(session.subscribe, session.getStatus);
}

export function usePendingSignIn(): PendingSignIn | null {
  const session = useAuthSession();
  return useSyncExternalStore(session.subscribe, session.getPendingSignIn);
}
