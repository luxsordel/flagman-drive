/**
 * Map settings of the client app. The tile provider is one line of config (ADR-0003):
 * set `EXPO_PUBLIC_MAP_STYLE_URL` to switch it.
 */

/** Free OpenStreetMap tiles without a key, the same style as the admin panel's maps. */
const defaultStyleUrl = 'https://tiles.openfreemap.org/styles/liberty';

export function mapStyleUrl(): string {
  const configured = process.env.EXPO_PUBLIC_MAP_STYLE_URL;
  return configured ? configured : defaultStyleUrl;
}

export interface LatLon {
  lat: number;
  lon: number;
}

/** Where the map opens without the client's location: the centre of Minsk. */
export const minskCenter: LatLon = { lat: 53.9023, lon: 27.5615 };
export const cityZoom = 12;
export const nearbyZoom = 14;

/** Cars move and get rented while the map is open. */
export const refreshIntervalMs = 30_000;
/** A pan is a burst of camera events: only the place where it stops is loaded. */
export const areaSettleMs = 400;
/** The API caps a page at 200; a phone screen never needs more markers than this. */
export const carsPerArea = 200;
