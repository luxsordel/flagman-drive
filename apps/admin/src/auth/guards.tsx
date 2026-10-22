import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'react-router';

import { ForbiddenPage } from '../pages/ForbiddenPage';
import { useAuth, useStaff } from './context';
import type { StaffRole } from './staff';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { state } = useAuth();

  if (state.status === 'loading') {
    return (
      <p role="status" className="page-status muted">
        {t('auth.loading')}
      </p>
    );
  }
  if (state.status === 'signedOut') return <Navigate to="/login" replace />;
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
