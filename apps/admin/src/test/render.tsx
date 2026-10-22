import { render, type RenderResult } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';

import { AuthProvider } from '../auth/AuthProvider';
import { createStaffSession } from '../auth/session';
import type { Staff } from '../auth/staff';
import { routes } from '../routes';
import { fakeStaffAuthApi, testStaff } from './auth';

/**
 * Renders the whole app at `path`, with the same routes as in the browser, signed in as `staff`
 * (an admin by default; null for no session). The session is restored asynchronously, so use `findBy*`.
 */
export function renderAt(
  path: string,
  staff: Staff | null = testStaff(),
): RenderResult & { api: ReturnType<typeof fakeStaffAuthApi> } {
  const api = fakeStaffAuthApi(staff);
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const result = render(
    <AuthProvider session={createStaffSession(api)}>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
  return { ...result, api };
}
