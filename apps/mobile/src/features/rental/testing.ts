import type { LatLon } from '../map/mapConfig';
import type {
  DamageSide,
  Debt,
  Inspection,
  InspectionStage,
  PhotoUpload,
  Rental,
  RentalApi,
  RentalCommand,
  RentalResult,
} from './rentalApi';

/** Test doubles shared by the rental tests. */

export const serverNow = '2026-10-26T12:00:00Z';

export function rental(overrides: Partial<Rental> = {}): Rental {
  return {
    id: 'rental-1',
    status: 'reserved',
    car: {
      id: 'car-1',
      brand: 'Geely',
      model: 'Geometry C',
      plate: '1234 AB-7',
      photo_url: null,
      lat: 53.9023,
      lon: 27.5615,
      battery_pct: 80,
      range_km: 320,
      doors_locked: true,
    },
    tariff: { driving_per_minute: 39, parked_per_minute: 12, waiting_per_minute: 12 },
    reserved_at: '2026-10-26T11:55:00Z',
    free_wait_until: '2026-10-26T12:10:00Z',
    expires_at: '2026-10-26T12:40:00Z',
    trip_started_at: null,
    ended_at: null,
    cancel_reason: null,
    inspection_submitted: false,
    after_inspection_submitted: false,
    cost: { lines: [], total: 0 },
    warnings: [],
    charge_status: null,
    receipt: null,
    as_of: serverNow,
    ...overrides,
  };
}

export function inspection(photos = 0, overrides: Partial<Inspection> = {}): Inspection {
  return {
    stage: 'before',
    photos: Array.from({ length: photos }, (_, index) => ({
      id: `photo-${String(index)}`,
      uploaded_at: serverNow,
    })),
    damages: [],
    submitted_at: null,
    min_photos: 4,
    max_photos: 8,
    ...overrides,
  };
}

export const ok = <T>(value: T): RentalResult<T> => ({ ok: true, value });

interface PendingUpload {
  photoUri: string;
  onProgress: ((sent: number) => void) | undefined;
  aborted: boolean;
  finish: (result: RentalResult<Inspection>) => void;
}

/** RentalApi whose uploads stay open until the test finishes them. */
export function fakeRentalApi() {
  const uploads: PendingUpload[] = [];
  const api = {
    current: jest.fn<Promise<RentalResult<Rental | null>>, []>(() => Promise.resolve(ok(null))),
    get: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    reserve: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    cancel: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    inspection: jest.fn<Promise<RentalResult<Inspection>>, [string, InspectionStage]>(),
    addDamage: jest.fn<
      Promise<RentalResult<Inspection>>,
      [string, InspectionStage, DamageSide, string]
    >(),
    submitInspection: jest.fn<Promise<RentalResult<Inspection>>, [string, InspectionStage]>(),
    command: jest.fn<Promise<RentalResult<Rental>>, [string, RentalCommand, LatLon]>(),
    park: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    resume: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    finish: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    payDebt: jest.fn<Promise<RentalResult<Rental>>, [string]>(),
    debts: jest.fn<Promise<RentalResult<Debt[]>>, []>(() => Promise.resolve(ok([]))),
    uploadPhoto: jest.fn(
      (
        _id: string,
        _stage: InspectionStage,
        photoUri: string,
        onProgress?: (sent: number) => void,
      ): PhotoUpload => {
        let finish: (result: RentalResult<Inspection>) => void = () => undefined;
        const done = new Promise<RentalResult<Inspection>>((resolve) => {
          finish = resolve;
        });
        const upload: PendingUpload = { photoUri, onProgress, aborted: false, finish };
        uploads.push(upload);
        return {
          done,
          abort: () => {
            upload.aborted = true;
            finish({ ok: false, error: null });
          },
        };
      },
    ),
  } satisfies RentalApi;
  return { api, uploads };
}

export const nearCar: LatLon = { lat: 53.9025, lon: 27.5615 };

/** A trip that has ended and been paid, with its receipt. */
export function paidRental(overrides: Partial<Rental> = {}): Rental {
  const cost = {
    lines: [
      { kind: 'waiting' as const, minutes: 2, free_minutes: 15, per_minute: 12, amount: 24 },
      { kind: 'driving' as const, minutes: 20, free_minutes: 0, per_minute: 39, amount: 780 },
      { kind: 'parked' as const, minutes: 5, free_minutes: 0, per_minute: 12, amount: 60 },
    ],
    total: 864,
  };
  return rental({
    status: 'paid',
    trip_started_at: '2026-10-26T11:30:00Z',
    ended_at: '2026-10-26T11:55:00Z',
    inspection_submitted: true,
    after_inspection_submitted: true,
    cost,
    charge_status: 'paid',
    receipt: {
      rental_id: 'rental-1',
      tariff_version_id: 'tariff-1',
      cost,
      card_brand: 'visa',
      card_last4: '4242',
      issued_at: '2026-10-26T11:55:00Z',
    },
    ...overrides,
  });
}
