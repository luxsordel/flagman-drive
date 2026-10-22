import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { AuthContext, type AuthContextValue, type AuthState } from './context';
import type { StaffSession } from './session';

export function AuthProvider({
  session,
  children,
}: {
  session: StaffSession;
  children: ReactNode;
}) {
  const [state, setState] = useState<AuthState>({ status: 'loading' });

  useEffect(() => {
    let active = true;
    const unsubscribe = session.onSignedOut(() => {
      setState({ status: 'signedOut' });
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

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      signOut: async () => {
        try {
          await session.signOut();
        } catch {
          // On a shared computer the next reload would quietly sign the same person back in.
          setState({ status: 'signedOut', logoutFailed: true });
        }
      },
    }),
    [state, session],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
