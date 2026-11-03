import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';
import { minskDayStart, nextDay } from '../audit/minskTime';
import type { PhotoUrl } from '../support/api';

export type FineType = components['schemas']['FineTypeVersionOut'];
export type Fine = components['schemas']['StaffFineOut'];
export type FineStatus = components['schemas']['FineStatus'];
export type FineEventAction = components['schemas']['FineEventAction'];
export type RefundStatus = components['schemas']['RefundStatus'];
export interface FineTerms {
  name_ru: string;
  name_en: string;
  /** Kopecks of BYN. */
  amount: number;
}

export const fineStatuses: readonly FineStatus[] = ['pending', 'paid', 'unpaid', 'cancelled'];
// The same limits as the API's: the form says so before the request does.
export const fineCommentMaxLength = 1000;
export const fineReasonMaxLength = 500;
export const fineNameMaxLength = 100;
export const fineAmountMaxKopecks = 1_000_000;
export const fineCodePattern = /^[a-z][a-z0-9_]{1,39}$/;

export interface FineFilter {
  status?: FineStatus;
  fineTypeId?: string;
  userId?: string;
  rentalId?: string;
  /** Minsk calendar days `YYYY-MM-DD`, both inclusive. */
  from?: string;
  to?: string;
}

export interface FinePage {
  items: Fine[];
  next_cursor?: string | null;
}

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface FinesApi {
  /** The catalog: every type with its current version, by code. */
  types(): Promise<FineType[]>;
  createType(code: string, terms: FineTerms): Promise<FineType>;
  /** A new version from now on; `basedOn` guards against overwriting a newer one. */
  updateType(fineTypeId: string, basedOn: number, terms: FineTerms): Promise<FineType>;
  list(filter: FineFilter, cursor?: string): Promise<FinePage>;
  charge(rentalId: string, fineTypeId: string, comment: string): Promise<Fine>;
  cancel(fineId: string, reason: string): Promise<Fine>;
  addPhoto(fineId: string, photo: Blob): Promise<Fine>;
  photoUrl(fineId: string, photoId: string): Promise<PhotoUrl>;
}

export function createFinesApi(client: AdminApiClient): FinesApi {
  return {
    async types() {
      return unwrap(await client.GET('/admin/fine-types')).items;
    },
    async createType(code, terms) {
      return unwrap(await client.POST('/admin/fine-types', { body: { code, ...terms } }));
    },
    async updateType(fineTypeId, basedOn, terms) {
      return unwrap(
        await client.PUT('/admin/fine-types/{fine_type_id}', {
          params: { path: { fine_type_id: fineTypeId } },
          body: { based_on_version: basedOn, ...terms },
        }),
      );
    },
    async list(filter, cursor) {
      return unwrap(
        await client.GET('/admin/support/fines', {
          params: {
            query: {
              ...(cursor ? { cursor } : {}),
              ...(filter.status ? { status: [filter.status] } : {}),
              ...(filter.fineTypeId ? { fine_type_id: [filter.fineTypeId] } : {}),
              ...(filter.userId ? { user_id: filter.userId } : {}),
              ...(filter.rentalId ? { rental_id: filter.rentalId } : {}),
              ...(filter.from ? { created_from: minskDayStart(filter.from) } : {}),
              ...(filter.to ? { created_to: minskDayStart(nextDay(filter.to)) } : {}),
            },
          },
        }),
      );
    },
    async charge(rentalId, fineTypeId, comment) {
      return unwrap(
        await client.POST('/admin/support/fines', {
          body: { rental_id: rentalId, fine_type_id: fineTypeId, comment },
        }),
      );
    },
    async cancel(fineId, reason) {
      return unwrap(
        await client.POST('/admin/support/fines/{fine_id}/cancel', {
          params: { path: { fine_id: fineId } },
          body: { reason },
        }),
      );
    },
    async addPhoto(fineId, photo) {
      return unwrap(
        await client.POST('/admin/support/fines/{fine_id}/photos', {
          params: { path: { fine_id: fineId } },
          // The schema types the raw body as a string; the bytes go as they are.
          body: '',
          bodySerializer: () => photo,
          headers: { 'Content-Type': photo.type || 'image/jpeg' },
        }),
      );
    },
    async photoUrl(fineId, photoId) {
      return unwrap(
        await client.GET('/admin/support/fines/{fine_id}/photos/{photo_id}/url', {
          params: { path: { fine_id: fineId, photo_id: photoId } },
        }),
      );
    },
  };
}
