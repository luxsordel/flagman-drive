import type { components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import { readPhoto } from '../kyc/readPhoto';
import { sendPhoto, type PhotoBody } from '../kyc/sendPhoto';
import type { LatLon } from '../map/mapConfig';
import { rentalErrorFromResponse, type RentalError } from './rentalErrors';

export type Rental = components['schemas']['RentalOut'];
export type RentalStatus = components['schemas']['RentalStatus'];
export type Inspection = components['schemas']['InspectionOut'];
export type DamageSide = components['schemas']['DamageSide'];
export type RentalCommand = components['schemas']['RentalCommand'];
export type InspectionStage = components['schemas']['InspectionStage'];
export type Receipt = components['schemas']['ReceiptOut'];
export type Debt = components['schemas']['DebtOut'];

export type RentalResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: RentalError }
  /** The client cancelled the upload: nothing to show. */
  | { ok: false; error: null };

export interface PhotoUpload {
  done: Promise<RentalResult<Inspection>>;
  abort: () => void;
}

export interface RentalApi {
  current(): Promise<RentalResult<Rental | null>>;
  get(id: string): Promise<RentalResult<Rental>>;
  reserve(carId: string): Promise<RentalResult<Rental>>;
  cancel(id: string): Promise<RentalResult<Rental>>;
  inspection(id: string, stage: InspectionStage): Promise<RentalResult<Inspection>>;
  uploadPhoto(
    id: string,
    stage: InspectionStage,
    photoUri: string,
    onProgress?: (sent: number) => void,
  ): PhotoUpload;
  addDamage(
    id: string,
    stage: InspectionStage,
    side: DamageSide,
    comment: string,
  ): Promise<RentalResult<Inspection>>;
  submitInspection(id: string, stage: InspectionStage): Promise<RentalResult<Inspection>>;
  /** `at` is the client's location: sent only for the distance, never kept by the API. */
  command(id: string, command: RentalCommand, at: LatLon): Promise<RentalResult<Rental>>;
  park(id: string): Promise<RentalResult<Rental>>;
  resume(id: string): Promise<RentalResult<Rental>>;
  /** Checks and locks the car, then pays; a repeat never charges twice. */
  finish(id: string): Promise<RentalResult<Rental>>;
  /** One more try at the debt of the rental, e.g. after another card was bound. */
  payDebt(id: string): Promise<RentalResult<Rental>>;
  debts(): Promise<RentalResult<Debt[]>>;
}

export interface RentalApiOptions {
  /** Client that already sends the access token. */
  api: ApiClient;
  baseUrl: string;
  /** Null once signed out; throws when the API cannot be reached (see AuthSession). */
  getAccessToken: () => Promise<string | null>;
  loadPhoto?: (uri: string) => Promise<PhotoBody>;
  createXhr?: () => XMLHttpRequest;
}

type Sent<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

export function createRentalApi({
  api,
  baseUrl,
  getAccessToken,
  loadPhoto = readPhoto,
  createXhr,
}: RentalApiOptions): RentalApi {
  const origin = baseUrl.replace(/\/+$/, '');
  const rental = (id: string) => ({ params: { path: { rental_id: id } } });
  const inspection = (id: string, stage: InspectionStage) => ({
    params: { path: { rental_id: id, stage } },
  });
  return {
    current: () => send(api.GET('/rentals/current'), (body) => body.rental),
    get: (id) => send(api.GET('/rentals/{rental_id}', rental(id)), (body) => body),
    reserve: (carId) => send(api.POST('/rentals', { body: { car_id: carId } }), (body) => body),
    cancel: (id) => send(api.POST('/rentals/{rental_id}/cancel', rental(id)), (body) => body),
    inspection: (id, stage) =>
      send(
        api.GET('/rentals/{rental_id}/inspections/{stage}', inspection(id, stage)),
        (body) => body,
      ),
    addDamage: (id, stage, side, comment) =>
      send(
        api.POST('/rentals/{rental_id}/inspections/{stage}/damages', {
          ...inspection(id, stage),
          body: { side, comment },
        }),
        (body) => body,
      ),
    submitInspection: (id, stage) =>
      send(
        api.POST('/rentals/{rental_id}/inspections/{stage}/submit', inspection(id, stage)),
        (body) => body,
      ),
    park: (id) => send(api.POST('/rentals/{rental_id}/park', rental(id)), (body) => body),
    resume: (id) => send(api.POST('/rentals/{rental_id}/resume', rental(id)), (body) => body),
    finish: (id) => send(api.POST('/rentals/{rental_id}/finish', rental(id)), (body) => body),
    payDebt: (id) => send(api.POST('/rentals/{rental_id}/pay', rental(id)), (body) => body),
    debts: () => send(api.GET('/rentals/debts'), (body) => body.debts),
    command: (id, command, at) =>
      send(
        api.POST('/rentals/{rental_id}/commands', {
          ...rental(id),
          body: { command, lat: at.lat, lon: at.lon },
        }),
        (body) => body,
      ),
    uploadPhoto(id, stage, photoUri, onProgress) {
      const controller = new AbortController();
      const { signal } = controller;
      const done = (async (): Promise<RentalResult<Inspection>> => {
        try {
          const token = await getAccessToken();
          if (token === null) return { ok: false, error: { kind: 'sessionExpired' } };
          const photo = await loadPhoto(photoUri).catch(() => null);
          if (signal.aborted) return { ok: false, error: null };
          if (photo === null) return { ok: false, error: { kind: 'unsupported' } };
          const response = await sendPhoto({
            url: `${origin}/rentals/${encodeURIComponent(id)}/inspections/${stage}/photos`,
            method: 'POST',
            accessToken: token,
            photo,
            signal,
            ...(onProgress ? { onProgress } : {}),
            ...(createXhr ? { createXhr } : {}),
          });
          if (response.status === 200) return { ok: true, value: response.body as Inspection };
          return {
            ok: false,
            error: rentalErrorFromResponse(response.body, response.status, response.retryAfter),
          };
        } catch {
          return signal.aborted
            ? { ok: false, error: null }
            : { ok: false, error: { kind: 'network' } };
        }
      })();
      return {
        done,
        abort: () => {
          controller.abort();
        },
      };
    },
  };
}

async function send<T, R>(request: Sent<T>, pick: (data: T) => R): Promise<RentalResult<R>> {
  try {
    const { data, error, response } = await request;
    if (data !== undefined) return { ok: true, value: pick(data) };
    return {
      ok: false,
      error: rentalErrorFromResponse(error, response.status, response.headers.get('Retry-After')),
    };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
