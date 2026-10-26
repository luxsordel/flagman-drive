import type { MapErrorKind } from './mapErrors';
import type { MapState } from './mapStore';

export interface MapNotice {
  kind: 'error' | 'info';
  text: `map.errors.${MapErrorKind}` | 'map.loading' | 'map.empty' | 'map.locationDenied';
}

/** One line under the map at most: an error first, then why the map is empty, then the location. */
export function mapNotice(state: MapState, locationDenied: boolean): MapNotice | null {
  if (state.error !== null) return { kind: 'error', text: `map.errors.${state.error.kind}` };
  if (!state.carsLoaded) return state.loadingCars ? { kind: 'info', text: 'map.loading' } : null;
  if (state.cars.length === 0) return { kind: 'info', text: 'map.empty' };
  if (locationDenied) return { kind: 'info', text: 'map.locationDenied' };
  return null;
}
