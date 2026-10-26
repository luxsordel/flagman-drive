import type { Palette } from '@flagman/design-tokens';

import type { MapArea, MapCar, MapZone } from './mapApi';
import type { LatLon } from './mapConfig';

/** One contract for CarMap.tsx (iOS, Android) and CarMap.web.tsx; the screen renders either. */
export interface CarMapProps {
  cars: readonly MapCar[];
  zones: readonly MapZone[];
  selectedId: string | null;
  /** Where the camera flies once, e.g. when the client's location becomes known. */
  focus: LatLon | null;
  showUserLocation: boolean;
  palette: Palette;
  /** For screen readers: the map itself has no text. */
  label: string;
  onAreaChange: (area: MapArea) => void;
  onCarPress: (id: string) => void;
  onMapPress: () => void;
}
