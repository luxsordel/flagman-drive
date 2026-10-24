import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { AuthContext, type AuthContextValue, type AuthState } from './context';
import type { AccessToken, StaffSession } from './session';

export function AuthProvider({
  session,
  children,
}: {
  session: StaffSession;
  children: ReactNode;
}) {
  const [state, setState] = useState<AuthState>({ status: 'loading' });
  // The session reports every end the same way; this tells a sign-out from an expired session.
  const signingOut = useRef(false);

  useEffect(() => {
    let active = true;
    const unsubscribe = session.onSignedOut(() => {
      setState(
        signingOut.current ? { status: 'signedOut', byUser: true } : { status: 'signedOut' },
      );
    });
    // An unreachable API also lands on the sign-in page: there is nothing to show without a session.
    session.restore().then(
      (staff) => {
        if (active) setState(staff ? { status: 'signedIn', staff } : { status: 'signedOut' });
      },
      () => {
        if (active) setState({ status: 'signedOut' });
      },
    );
    return () => {
      active = false;
      unsubscribe();
    };
  }, [session]);

  // Stable while the session is the same: pages keep it in effect dependencies, and a new one on
  // every auth state change would make them reload.
  const signOut = useCallback(async () => {
    signingOut.current = true;
    try {
      await session.signOut();
    } catch {
      // On a shared computer the next reload would quietly sign the same person back in.
      setState({ status: 'signedOut', byUser: true, logoutFailed: true });
    } finally {
      signingOut.current = false;
    }
  }, [session]);

  const signIn = useCallback(
    async (token: AccessToken) => {
      const staff = await session.signIn(token);
      setState({ status: 'signedIn', staff });
    },
    [session],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ state, signIn, signOut }),
    [state, signIn, signOut],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
