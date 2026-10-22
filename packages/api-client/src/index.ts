import createClient, { type ClientOptions } from 'openapi-fetch';

import type { components, paths } from './generated/schema';

export type { components, paths };

/** Problem Details body that every error response carries (ADR-0006). */
export type Problem = components['schemas']['Problem'];

export interface ApiClientOptions {
  /** API origin, e.g. `http://localhost:8000`. */
  baseUrl: string;
  /** Returns the access token for the `Authorization` header, or null when signed out. */
  getAccessToken?: () => string | null | Promise<string | null>;
  /** Override for tests or React Native polyfills. */
  fetch?: ClientOptions['fetch'];
}

export function createApiClient(options: ApiClientOptions): ReturnType<typeof createClient<paths>> {
  const { baseUrl, getAccessToken, fetch } = options;
  const client = createClient<paths>({ baseUrl, ...(fetch ? { fetch } : {}) });
  if (getAccessToken) {
    client.use({
      async onRequest({ request }) {
        const token = await getAccessToken();
        if (token) request.headers.set('Authorization', `Bearer ${token}`);
        return request;
      },
    });
  }
  return client;
}
