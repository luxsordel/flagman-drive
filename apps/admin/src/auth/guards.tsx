import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation } from 'react-router';

import { ForbiddenPage } from '../pages/ForbiddenPage';
import { useAuth, useStaff } from './context';
import { loginRedirect } from './redirect';
import type { StaffRole } from './staff';

/** Shown while the session is being restored from the refresh cookie. */
export function AuthLoading() {
  const { t } = useTranslation();
  return (
    <p role="status" className="page-status muted">
      {t('auth.loading')}
    </p>
  );
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === 'loading') return <AuthLoading />;
  if (state.status === 'signedOut') {
    // After an expired session the sign-in page returns here. After signing out it does not:
    // the next person at this computer may have another role and no access to the page.
    return (
      <Navigate to="/login" replace state={state.byUser ? undefined : loginRedirect(location)} />
    );
  }
  return children;
}

/** Hides a section from the wrong role. Only a convenience: the API checks the role on every call. */
export function RequireRole({
  roles,
  children,
}: {
  roles: readonly StaffRole[];
  children: ReactNode;
}) {
  const staff = useStaff();
  return roles.includes(staff.role) ? children : <ForbiddenPage />;
}
