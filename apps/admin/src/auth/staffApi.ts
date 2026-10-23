import type { components } from '@flagman/api-client';

import { toApiError, unwrap, type AdminApiClient } from '../api/client';
import type { AccessToken, StaffAuthApi } from './session';

export type StaffChallenge = components['schemas']['StaffChallengeIssued'];
export type TotpEnrollment = components['schemas']['StaffTotpEnrollment'];

/**
 * The sign-in steps before a session exists (#32): password, then the first 2FA setup if needed,
 * then the TOTP code. The challenge token links the steps; keep it in memory only.
 * Each call rejects with ApiError when the API refuses, with NetworkError when it is
 * unreachable.
 */
export interface StaffSignInApi {
  checkPassword(email: string, password: string): Promise<StaffChallenge>;
  startTotpEnrollment(challengeToken: string): Promise<TotpEnrollment>;
  verifyTotp(challengeToken: string, code: string): Promise<AccessToken>;
}

const toAccessToken = (data: components['schemas']['StaffAccessToken']): AccessToken => ({
  accessToken: data.access_token,
  expiresInSeconds: data.expires_in,
});

/** `client` must not add the access token: these endpoints run before the session has one. */
export function createStaffAuthApi(client: AdminApiClient): StaffAuthApi & StaffSignInApi {
  return {
    async refresh() {
      // The body is required but empty: the refresh token travels in the HttpOnly cookie.
      const { data, error, response } = await client.POST('/staff/auth/token/refresh', {
        body: {},
      });
      if (data) return toAccessToken(data);
      if (response.status === 401) return 'rejected';
      throw toApiError(response, error);
    },

    async me(accessToken) {
      return unwrap(
        await client.GET('/staff/me', { headers: { Authorization: `Bearer ${accessToken}` } }),
      );
    },

    async logout() {
      const { error, response } = await client.POST('/staff/auth/logout', { body: {} });
      if (!response.ok) throw toApiError(response, error);
    },

    async checkPassword(email, password) {
      return unwrap(await client.POST('/staff/auth/password', { body: { email, password } }));
    },

    async startTotpEnrollment(challengeToken) {
      return unwrap(
        await client.POST('/staff/auth/totp/enrollment', {
          body: { challenge_token: challengeToken },
        }),
      );
    },

    async verifyTotp(challengeToken, code) {
      return toAccessToken(
        unwrap(
          await client.POST('/staff/auth/totp', {
            body: { challenge_token: challengeToken, code },
          }),
        ),
      );
    },
  };
}
