import { createApiClient } from '@flagman/api-client';

import type { StaffSession } from '../auth/session';

export type AdminApiClient = ReturnType<typeof createApiClient>;

const problemTypePrefix = 'urn:flagman:problem:';

/** The API answered with an error status; network failures reject with the fetch error instead. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    /** The Problem Details type without the `urn:flagman:problem:` prefix, e.g. `totp-invalid`. */
    readonly problem?: string,
    /** From `Retry-After` of a 429. */
    readonly retryAfterSeconds?: number,
  ) {
    super(`API request failed with status ${String(status)}`);
    this.name = 'ApiError';
  }

  /**
   * The API itself did not answer: a proxy or load balancer in front of a stopped API sends these
   * without a Problem body. The API's own 503s carry one and mean something specific.
   */
  get unavailable(): boolean {
    return this.problem === undefined && [502, 503, 504].includes(this.status);
  }
}

/** Builds an ApiError from a failed response and the body openapi-fetch parsed from it. */
export function toApiError(response: Response, body: unknown): ApiError {
  const type =
    typeof body === 'object' && body !== null && 'type' in body && typeof body.type === 'string'
      ? body.type
      : undefined;
  // Delta-seconds only, as the API sends it; an HTTP date or anything else is ignored.
  const retryAfter = response.headers.get('Retry-After')?.trim() ?? '';
  const retryAfterSeconds = /^\d+$/.test(retryAfter) ? Number(retryAfter) : 0;
  return new ApiError(
    response.status,
    type?.startsWith(problemTypePrefix) ? type.slice(problemTypePrefix.length) : undefined,
    retryAfterSeconds > 0 ? retryAfterSeconds : undefined,
  );
}

/**
 * The data of a successful openapi-fetch call; throws ApiError for an error status.
 * Only for endpoints that answer with a body: a 204 has no data and would throw too.
 */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data === undefined) throw toApiError(result.response, result.error);
  return result.data;
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

/**
 * For the staff auth endpoints. They authenticate with the refresh cookie or a sign-in challenge,
 * and the session that would supply the access token is built on top of them.
 */
export function createAuthApiClient(): AdminApiClient {
  return createApiClient({ baseUrl: window.location.origin });
}
