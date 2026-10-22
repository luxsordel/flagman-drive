// Only the weights styles.css uses (headings 600/700, text 400/600) and only the scripts of ru and en.
import '@fontsource/oswald/cyrillic-600.css';
import '@fontsource/oswald/latin-600.css';
import '@fontsource/oswald/cyrillic-700.css';
import '@fontsource/oswald/latin-700.css';
import '@fontsource/open-sans/cyrillic-400.css';
import '@fontsource/open-sans/latin-400.css';
import '@fontsource/open-sans/cyrillic-600.css';
import '@fontsource/open-sans/latin-600.css';
import './i18n';
import './styles.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { AuthProvider } from './auth/AuthProvider';
import { pendingStaffAuthApi } from './auth/pendingApi';
import { createStaffSession } from './auth/session';
import { routes } from './routes';
import { applyTheme, currentTheme, installTheme } from './theme';

installTheme();
applyTheme(currentTheme());

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no #root element');

const session = createStaffSession(pendingStaffAuthApi);

createRoot(root).render(
  <StrictMode>
    <AuthProvider session={session}>
      <RouterProvider router={createBrowserRouter(routes)} />
    </AuthProvider>
  </StrictMode>,
);
