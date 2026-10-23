import { createContext, useContext } from 'react';

import type { AccessToken } from './session';
import type { Staff } from './staff';

export type AuthState =
  | { status: 'loading' }
  // byUser: the staff member signed out, the session did not just expire.
  // logoutFailed: the panel forgot the session, but the server and its refresh cookie may keep it.
  | { status: 'signedOut'; byUser?: true; logoutFailed?: true }
  | { status: 'signedIn'; staff: Staff };

export interface AuthContextValue {
  state: AuthState;
  /** Finishes a sign-in that passed 2FA; rejects when the staff profile cannot be loaded. */
  signIn: (token: AccessToken) => Promise<void>;
  /** Never rejects: a failed server sign-out shows up as `logoutFailed` instead. */
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error('useAuth is used outside of AuthProvider');
  return auth;
}

/** The signed-in staff member; only for pages behind RequireAuth. */
export function useStaff(): Staff {
  const { state } = useAuth();
  if (state.status !== 'signedIn') throw new Error('useStaff is used outside of RequireAuth');
  return state.staff;
}
