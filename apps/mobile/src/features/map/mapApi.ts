import type { components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import type { LatLon } from './mapConfig';
import { mapErrorFromResponse, type MapError } from './mapErrors';

export type MapCar = components['schemas']['MapCarOut'];
type MapZoneOut = components['schemas']['MapZoneOut'];
/** As the client returns it: response bodies come with plain arrays, not `[lon, lat]` tuples. */
export type MapZone = Omit<MapZoneOut, 'polygon'> & {
  polygon: { type: 'Polygon'; coordinates: number[][][] };
};

/** The visible part of the map; edges are inclusive. */
export interface MapArea {
  minLat: number;
  minLon: number;
  maxLat: number;
  maxLon: number;
}

export type MapResult<T> = { ok: true; value: T } | { ok: false; error: MapError };

/** Failures come back as `{ ok: false }`, like ProfileApi; the store still guards against a throw. */
export interface MapApi {
  /** `near` is the client's location: sent only for the distance, never kept by the API. */
  listCars(area: MapArea, near: LatLon | null, limit: number): Promise<MapResult<MapCar[]>>;
  getCar(id: string, near: LatLon | null): Promise<MapResult<MapCar>>;
  listZones(): Promise<MapResult<MapZone[]>>;
}

type Sent<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

/** `api` must already send the access token. */
export function createMapApi(api: ApiClient): MapApi {
  return {
    listCars: (area, near, limit) =>
      send(
        api.GET('/map/cars', {
          params: {
            query: {
              min_lat: area.minLat,
              min_lon: area.minLon,
              max_lat: area.maxLat,
              max_lon: area.maxLon,
              ...near,
              limit,
            },
          },
        }),
        (list) => list.items,
      ),
    getCar: (id, near) =>
      send(
        api.GET('/map/cars/{car_id}', { params: { path: { car_id: id }, query: { ...near } } }),
        (car) => car,
      ),
    listZones: () => send(api.GET('/map/zones'), (list) => list.items),
  };
}

async function send<T, R>(request: Sent<T>, pick: (data: T) => R): Promise<MapResult<R>> {
  try {
    const { data, error } = await request;
    if (data !== undefined) return { ok: true, value: pick(data) };
    return { ok: false, error: mapErrorFromResponse(error) };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
