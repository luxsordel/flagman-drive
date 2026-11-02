import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';
import { minskDayStart, nextDay } from '../audit/minskTime';

export type StaffRental = components['schemas']['StaffRentalOut'];
export type StaffRentalItem = components['schemas']['StaffRentalItemOut'];
export type StaffRentalPage = components['schemas']['StaffRentalPageOut'];
export type ForceFinishResult = components['schemas']['ForceFinishResultOut'];
export type RentalStatus = components['schemas']['RentalStatus'];
export type InspectionStage = components['schemas']['InspectionStage'];
export type PhotoUrl = components['schemas']['PhotoUrlOut'];

export const rentalStatuses: readonly RentalStatus[] = [
  'reserved',
  'in_trip',
  'parked',
  'finished',
  'paid',
  'payment_failed',
  'cancelled',
];
/** The rental of a car right now: at most one, the database guarantees it. */
export const activeRentalStatuses: readonly RentalStatus[] = ['reserved', 'in_trip', 'parked'];
/** STAFF_REASON_MAX_LENGTH and ADJUSTMENT_MAX of the API (rentals/domain.py, schemas.py). */
export const staffReasonMaxLength = 500;
export const adjustmentMaxKopecks = 100_000;

export interface RentalFilter {
  statuses?: readonly RentalStatus[];
  userId?: string;
  carId?: string;
  /** Calendar days in Minsk, `YYYY-MM-DD`, both included. */
  from?: string;
  to?: string;
}

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface RentalsApi {
  list(filter: RentalFilter, cursor?: string): Promise<StaffRentalPage>;
  rental(id: string): Promise<StaffRental>;
  /** A short-lived link; the API writes every one to the audit log. */
  photoUrl(rentalId: string, stage: InspectionStage, photoId: string): Promise<PhotoUrl>;
  forceFinish(id: string, reason: string): Promise<ForceFinishResult>;
  /** `amount` in kopecks, negative to lower the total. */
  adjust(id: string, amount: number, comment: string): Promise<StaffRental>;
  retryRefunds(id: string): Promise<StaffRental>;
}

export function createRentalsApi(client: AdminApiClient): RentalsApi {
  const rentalPath = (id: string) => ({ params: { path: { rental_id: id } } });
  return {
    async list(filter, cursor) {
      return unwrap(
        await client.GET('/admin/rentals', {
          params: {
            query: {
              ...(filter.statuses?.length ? { status: [...filter.statuses] } : {}),
              ...(filter.userId ? { user_id: filter.userId } : {}),
              ...(filter.carId ? { car_id: filter.carId } : {}),
              ...(filter.from ? { reserved_from: minskDayStart(filter.from) } : {}),
              ...(filter.to ? { reserved_to: minskDayStart(nextDay(filter.to)) } : {}),
              ...(cursor ? { cursor } : {}),
            },
          },
        }),
      );
    },
    async rental(id) {
      return unwrap(await client.GET('/admin/rentals/{rental_id}', rentalPath(id)));
    },
    async photoUrl(rentalId, stage, photoId) {
      return unwrap(
        await client.GET('/admin/rentals/{rental_id}/inspections/{stage}/photos/{photo_id}/url', {
          params: { path: { rental_id: rentalId, stage, photo_id: photoId } },
        }),
      );
    },
    async forceFinish(id, reason) {
      return unwrap(
        await client.POST('/admin/rentals/{rental_id}/force-finish', {
          ...rentalPath(id),
          body: { reason },
        }),
      );
    },
    async adjust(id, amount, comment) {
      return unwrap(
        await client.POST('/admin/rentals/{rental_id}/adjustments', {
          ...rentalPath(id),
          body: { amount, comment },
        }),
      );
    },
    async retryRefunds(id) {
      return unwrap(await client.POST('/admin/rentals/{rental_id}/refunds/retry', rentalPath(id)));
    },
  };
}
