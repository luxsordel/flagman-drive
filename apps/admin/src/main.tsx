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

import { createAdminApiClient, createAuthApiClient } from './api/client';
import { ApiContext, type AdminApis } from './api/context';
import { createAuditLogApi } from './audit/api';
import { AuthProvider } from './auth/AuthProvider';
import { createStaffSession } from './auth/session';
import { createStaffAuthApi } from './auth/staffApi';
import { createFleetApi } from './fleet/api';
import { createFleetStream, fleetStreamUrl } from './fleet/stream';
import { createKycReviewApi } from './kyc/api';
import { routes } from './routes';
import { createStaffListApi } from './staff/api';
import { createTariffsApi } from './tariffs/api';
import { applyTheme, currentTheme, installTheme } from './theme';
import { createZonesApi } from './zones/api';

installTheme();
applyTheme(currentTheme());

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no #root element');

const staffAuth = createStaffAuthApi(createAuthApiClient());
const session = createStaffSession(staffAuth);
const client = createAdminApiClient(session);
const apis: AdminApis = {
  auditLog: createAuditLogApi(client),
  fleet: createFleetApi(client),
  fleetStream: createFleetStream({
    url: fleetStreamUrl(window.location),
    accessToken: () => session.accessToken(),
  }),
  kycReview: createKycReviewApi(client),
  staffList: createStaffListApi(client),
  staffSignIn: staffAuth,
  tariffs: createTariffsApi(client),
  zones: createZonesApi(client),
};

createRoot(root).render(
  <StrictMode>
    <AuthProvider session={session}>
      <ApiContext value={apis}>
        <RouterProvider router={createBrowserRouter(routes)} />
      </ApiContext>
    </AuthProvider>
  </StrictMode>,
);
