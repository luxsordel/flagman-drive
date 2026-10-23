import { createApiClient } from '@flagman/api-client';

import type { StaffSession } from '../auth/session';

export type AdminApiClient = ReturnType<typeof createApiClient>;

/** The API answered with an error status; network failures reject with the fetch error instead. */
export class ApiError extends Error {
  constructor(readonly status: number) {
    super(`API request failed with status ${String(status)}`);
    this.name = 'ApiError';
  }
}

/**
 * The API on the panel's own origin: the dev server proxies `/staff` and `/admin` to it
 * (vite.config.ts), so the refresh cookie stays first-party and no CORS is needed.
 */
export function createAdminApiClient(session: StaffSession): AdminApiClient {
  return createApiClient({
    baseUrl: window.location.origin,
    getAccessToken: () => session.accessToken(),
  });
}
