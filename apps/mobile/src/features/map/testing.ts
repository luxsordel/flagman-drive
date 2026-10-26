import type { MapApi, MapArea, MapCar, MapResult, MapZone } from './mapApi';
import type { LatLon } from './mapConfig';

/** Test doubles shared by the map tests. */

export function mapCar(overrides: Partial<MapCar> = {}): MapCar {
  return {
    id: 'car-1',
    brand: 'Geely',
    model: 'Geometry C',
    photo_url: 'https://cdn.test/geometry-c.png',
    lat: 53.9023,
    lon: 27.5615,
    battery_pct: 80,
    range_km: 320,
    distance_m: null,
    ...overrides,
  };
}

export function mapZone(overrides: Partial<MapZone> = {}): MapZone {
  return {
    id: 'zone-1',
    kind: 'allowed',
    polygon: {
      type: 'Polygon',
      coordinates: [
        [
          [27.55, 53.89],
          [27.57, 53.89],
          [27.57, 53.91],
          [27.55, 53.89],
        ],
      ],
    },
    ...overrides,
  };
}

export const area: MapArea = { minLat: 53.85, minLon: 27.45, maxLat: 53.95, maxLon: 27.65 };

export const ok = <T>(value: T): MapResult<T> => ({ ok: true, value });

export function fakeMapApi() {
  return {
    listCars: jest.fn<Promise<MapResult<MapCar[]>>, [MapArea, LatLon | null, number]>(),
    getCar: jest.fn<Promise<MapResult<MapCar>>, [string, LatLon | null]>(),
    listZones: jest.fn<Promise<MapResult<MapZone[]>>, []>(),
  } satisfies MapApi;
}

/** A promise the test resolves when it wants, to answer requests out of order. */
export function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}
