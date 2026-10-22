import { createApiClient } from '@flagman/api-client';
import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { Platform } from 'react-native';

import { AuthSession, type AuthStatus, type PendingSignIn } from './authSession';
import { createTokenStore } from './tokenStore';

const defaultApiUrl = 'http://localhost:8000';

/** An empty value, as in a copied `.env.example`, means "not set" rather than the app's own origin. */
export function resolveApiUrl(configured: string | undefined): string {
  const trimmed = configured?.trim() ?? '';
  return trimmed === '' ? defaultApiUrl : trimmed;
}

export function createAppAuthSession(): AuthSession {
  const api = createApiClient({
    // Public client setting, not a secret; inlined at build time, so read by its literal name.
    baseUrl: resolveApiUrl(process.env.EXPO_PUBLIC_API_URL),
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
