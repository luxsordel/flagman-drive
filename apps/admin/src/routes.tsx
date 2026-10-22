import type { RouteObject } from 'react-router';

import { RequireAuth, RequireRole } from './auth/guards';
import { AppLayout } from './layout/AppLayout';
import { sections } from './navigation';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { SectionPage } from './pages/SectionPage';

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      ...sections.map(({ key, path, roles }) => ({
        path,
        element: (
          <RequireRole roles={roles}>
            <SectionPage section={key} />
          </RequireRole>
        ),
      })),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
