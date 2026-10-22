import type { RouteObject } from 'react-router';

import { AppLayout } from './layout/AppLayout';
import { sections } from './navigation';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { SectionPage } from './pages/SectionPage';

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <AppLayout />,
    children: [
      ...sections.map(({ key, path }) => ({ path, element: <SectionPage section={key} /> })),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
