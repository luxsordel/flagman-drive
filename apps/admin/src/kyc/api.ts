import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';

export type KycQueue = components['schemas']['KycQueueOut'];
export type KycQueueItem = components['schemas']['KycQueueItemOut'];
export type KycApplication = components['schemas']['KycReviewOut'];
export type KycDocumentKind = components['schemas']['DocumentKind'];
export type KycDocumentUrl = components['schemas']['DocumentUrlOut'];

/**
 * QUEUE_SIZE_MAX of the API (kyc/schemas.py); the queue has no paging, so the page shows this many.
 * Change both together: a larger value than the API's is refused with 422 and the queue never loads.
 */
export const kycQueueSize = 100;

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface KycReviewApi {
  /** Applications waiting for a decision, the longest waiting first, and how many there are in all. */
  queue(limit?: number): Promise<KycQueue>;
  application(id: string): Promise<KycApplication>;
  /** A short-lived link to the photo; the API writes every one to the audit log. */
  documentUrl(id: string, kind: KycDocumentKind): Promise<KycDocumentUrl>;
  /** `licenseIssuedOn` is `YYYY-MM-DD`, read from the license photo. */
  approve(id: string, licenseIssuedOn: string): Promise<KycApplication>;
  reject(id: string, reason: string): Promise<KycApplication>;
}

export function createKycReviewApi(client: AdminApiClient): KycReviewApi {
  return {
    async queue(limit = kycQueueSize) {
      return unwrap(await client.GET('/admin/kyc/applications', { params: { query: { limit } } }));
    },
    async application(id) {
      return unwrap(
        await client.GET('/admin/kyc/applications/{application_id}', {
          params: { path: { application_id: id } },
        }),
      );
    },
    async documentUrl(id, kind) {
      return unwrap(
        await client.GET('/admin/kyc/applications/{application_id}/documents/{kind}/url', {
          params: { path: { application_id: id, kind } },
        }),
      );
    },
    async approve(id, licenseIssuedOn) {
      return unwrap(
        await client.POST('/admin/kyc/applications/{application_id}/approve', {
          params: { path: { application_id: id } },
          body: { license_issued_on: licenseIssuedOn },
        }),
      );
    },
    async reject(id, reason) {
      return unwrap(
        await client.POST('/admin/kyc/applications/{application_id}/reject', {
          params: { path: { application_id: id } },
          body: { reason },
        }),
      );
    },
  };
}
