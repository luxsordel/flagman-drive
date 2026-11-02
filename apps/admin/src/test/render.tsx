import { render, type RenderResult } from '@testing-library/react';
import { createMemoryRouter, RouterProvider, type InitialEntry } from 'react-router';

import { ApiContext, type AdminApis } from '../api/context';
import { AuthProvider } from '../auth/AuthProvider';
import { createStaffSession } from '../auth/session';
import type { Staff } from '../auth/staff';
import { routes } from '../routes';
import { fakeAuditLogApi } from './audit';
import { fakeClientsApi } from './clients';
import { fakeFleetApi, fakeFleetStream } from './fleet';
import { fakeStaffAuthApi, fakeStaffSignInApi, testStaff } from './auth';
import { fakeKycReviewApi } from './kyc';
import { fakeRentalsApi } from './rentals';
import { fakeStaffListApi } from './staff';
import { fakeSupportApi } from './support';
import { fakeTariffsApi } from './tariffs';
import { fakeZonesApi } from './zones';

/**
 * Renders the whole app at `path` (a location object to pass history state), with the same
 * routes as in the browser, signed in as `staff` (an admin by default; null for no session).
 * The session is restored asynchronously, so use `findBy*`.
 * Page APIs not given in `apis` answer with nothing found. `router` moves to another address.
 */
export function renderAt(
  path: InitialEntry,
  staff: Staff | null = testStaff(),
  apis: Partial<AdminApis> = {},
): RenderResult & {
  api: ReturnType<typeof fakeStaffAuthApi>;
  router: ReturnType<typeof createMemoryRouter>;
} {
  const api = fakeStaffAuthApi(staff);
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const result = render(
    <AuthProvider session={createStaffSession(api)}>
      <ApiContext
        value={{
          auditLog: fakeAuditLogApi(),
          clients: fakeClientsApi(),
          fleet: fakeFleetApi(),
          fleetStream: fakeFleetStream().open,
          kycReview: fakeKycReviewApi(),
          rentals: fakeRentalsApi(),
          staffList: fakeStaffListApi(),
          staffSignIn: fakeStaffSignInApi(),
          support: fakeSupportApi(),
          tariffs: fakeTariffsApi(),
          zones: fakeZonesApi(),
          ...apis,
        }}
      >
        <RouterProvider router={router} />
      </ApiContext>
    </AuthProvider>,
  );
  return { ...result, api, router };
}
