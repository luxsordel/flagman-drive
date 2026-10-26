import { createApiClient, parseRetryAfter, problemSlug } from '@flagman/api-client';

import type { StaffSession } from '../auth/session';

export type AdminApiClient = ReturnType<typeof createApiClient>;

/** The API answered with an error status; failures to reach it reject with NetworkError instead. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    /** The Problem Details type without the `urn:flagman:problem:` prefix, e.g. `totp-invalid`. */
    readonly problem?: string,
    /** From `Retry-After` of a 429. */
    readonly retryAfterSeconds?: number,
    /** The Problem's `detail`, for the problems whose detail is a code, e.g. a car's refusal reason. */
    readonly detail?: string,
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

/** The request did not reach the API: offline, DNS, a refused connection, CORS. */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super('The API could not be reached', { cause });
    this.name = 'NetworkError';
  }
}

// fetch rejects with a bare TypeError, which a bug in the panel throws too; this tells them apart.
const fetchOrNetworkError = (request: Request): Promise<Response> =>
  fetch(request).catch((error: unknown) => {
    throw new NetworkError(error);
  });

/** Builds an ApiError from a failed response and the body openapi-fetch parsed from it. */
export function toApiError(response: Response, body: unknown): ApiError {
  const retryAfterSeconds = parseRetryAfter(response.headers.get('Retry-After'));
  const slug = problemSlug(body);
  // Only a Problem's own detail: a proxy's error page is not ours to show.
  const detail =
    slug !== null && typeof body === 'object' && body !== null && 'detail' in body
      ? body.detail
      : undefined;
  return new ApiError(
    response.status,
    slug ?? undefined,
    // "0" asks for no wait at all: the form then shows no countdown.
    retryAfterSeconds ? retryAfterSeconds : undefined,
    typeof detail === 'string' ? detail : undefined,
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
    fetch: fetchOrNetworkError,
  });
}

/**
 * For the staff auth endpoints. They authenticate with the refresh cookie or a sign-in challenge,
 * and the session that would supply the access token is built on top of them.
 */
export function createAuthApiClient(): AdminApiClient {
  return createApiClient({ baseUrl: window.location.origin, fetch: fetchOrNetworkError });
}
