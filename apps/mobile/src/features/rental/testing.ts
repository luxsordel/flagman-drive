import type { LatLon } from '../map/mapConfig';
import type {
  DamageSide,
  Inspection,
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
    reserved_at: '2026-10-26T11:55:00Z',
    free_wait_until: '2026-10-26T12:10:00Z',
    expires_at: '2026-10-26T12:40:00Z',
    trip_started_at: null,
    ended_at: null,
    cancel_reason: null,
    inspection_submitted: false,
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
    inspection: jest.fn<Promise<RentalResult<Inspection>>, [string]>(),
    addDamage: jest.fn<Promise<RentalResult<Inspection>>, [string, DamageSide, string]>(),
    submitInspection: jest.fn<Promise<RentalResult<Inspection>>, [string]>(),
    command: jest.fn<Promise<RentalResult<Rental>>, [string, RentalCommand, LatLon]>(),
    uploadPhoto: jest.fn(
      (_id: string, photoUri: string, onProgress?: (sent: number) => void): PhotoUpload => {
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
