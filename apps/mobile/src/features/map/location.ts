import {
  Accuracy,
  getCurrentPositionAsync,
  PermissionStatus,
  requestForegroundPermissionsAsync,
  watchPositionAsync,
  type LocationSubscription,
} from 'expo-location';
import { useEffect, useState } from 'react';

import type { LatLon } from './mapConfig';

export type LocationStatus = 'asking' | 'granted' | 'denied';

export interface ClientLocation {
  status: LocationStatus;
  /** Null until the first fix, and always without the permission. */
  location: LatLon | null;
}

// Enough for "how far is the car": a finer fix drains the battery for nothing.
const watchOptions = { accuracy: Accuracy.Balanced, distanceInterval: 25 } as const;

/**
 * The client's location while the map is on screen, with their permission. Kept in memory only:
 * it is never stored or logged (docs/03-security.md). A refusal or a failure leaves the map
 * working without it.
 */
export function useClientLocation(): ClientLocation {
  const [state, setState] = useState<ClientLocation>({ status: 'asking', location: null });

  useEffect(() => {
    let active = true;
    // A call, not the flag itself: it flips in the cleanup, outside the flow TypeScript narrows.
    const mounted = () => active;
    let subscription: LocationSubscription | null = null;
    void (async () => {
      try {
        const { status } = await requestForegroundPermissionsAsync();
        if (!mounted()) return;
        if (status !== PermissionStatus.GRANTED) {
          setState({ status: 'denied', location: null });
          return;
        }
        setState({ status: 'granted', location: null });
        const watching = await watchPositionAsync(watchOptions, ({ coords }) => {
          setState({
            status: 'granted',
            location: { lat: coords.latitude, lon: coords.longitude },
          });
        });
        if (mounted()) subscription = watching;
        else watching.remove();
      } catch {
        // Location services off, or the browser refused: the same as a refusal for the map.
        if (mounted()) setState({ status: 'denied', location: null });
      }
    })();
    return () => {
      active = false;
      subscription?.remove();
    };
  }, []);

  return state;
}

export type LocationFix =
  | { kind: 'located'; location: LatLon }
  | { kind: 'denied' }
  /** Location services off or no fix in time. */
  | { kind: 'unavailable' };

/**
 * One precise fix for a command to the car: the server checks the distance to it, so the coarse
 * fix of the map could leave a client standing next to the car "too far". Never stored or logged.
 */
export async function locateOnce(): Promise<LocationFix> {
  try {
    const { status } = await requestForegroundPermissionsAsync();
    if (status !== PermissionStatus.GRANTED) return { kind: 'denied' };
    const { coords } = await getCurrentPositionAsync({ accuracy: Accuracy.High });
    return { kind: 'located', location: { lat: coords.latitude, lon: coords.longitude } };
  } catch {
    return { kind: 'unavailable' };
  }
}
