import createClient, { type ClientOptions } from 'openapi-fetch';

import type { components, paths } from './generated/schema';

export type { components, paths };

/** Problem Details body that every error response carries (ADR-0006). */
export type Problem = components['schemas']['Problem'];

/** Access and refresh tokens returned by sign-in and refresh. */
export type TokenPair = components['schemas']['TokenPair'];

export interface ApiClientOptions {
  /** API origin, e.g. `http://localhost:8000`. */
  baseUrl: string;
  /** Returns the access token for the `Authorization` header, or null when signed out. */
  getAccessToken?: () => string | null | Promise<string | null>;
  /** Override for tests or React Native polyfills. */
  fetch?: ClientOptions['fetch'];
  /**
   * `include` for a web client served from another origin than the API: without it the browser
   * neither sends nor stores the HttpOnly refresh cookie (ADR-0009).
   */
  credentials?: RequestCredentials;
}

export function createApiClient(options: ApiClientOptions): ReturnType<typeof createClient<paths>> {
  const { baseUrl, getAccessToken, fetch, credentials } = options;
  const client = createClient<paths>({
    baseUrl,
    ...(fetch ? { fetch } : {}),
    ...(credentials ? { credentials } : {}),
  });
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
