import type { Palette } from '@flagman/design-tokens';
import type { ExpressionSpecification } from 'maplibre-gl';

import type { CarStatus, FleetCar } from './api';

export const carsSourceId = 'fleet-cars';
export const carsLayerId = 'fleet-cars';

/**
 * Green is free to rent and amber is held by a client, as on the client's own map; blue drives,
 * red is out of service and grey is out of reach. The list beside the map uses the same colors.
 */
export function statusColor(status: CarStatus, palette: Palette): string {
  switch (status) {
    case 'available':
      return palette.success;
    case 'reserved':
      return palette.warning;
    case 'in_rental':
      return palette.primary;
    case 'service':
      return palette.danger;
    case 'offline':
      return palette.inkMuted;
  }
}

export const statusColorExpression = (palette: Palette): ExpressionSpecification => [
  'match',
  ['get', 'status'],
  'available',
  statusColor('available', palette),
  'reserved',
  statusColor('reserved', palette),
  'in_rental',
  statusColor('in_rental', palette),
  'service',
  statusColor('service', palette),
  statusColor('offline', palette),
];

// The selected car is larger, a car with stale telemetry fades: it may be elsewhere by now.
export const carRadius: ExpressionSpecification = ['case', ['get', 'selected'], 10, 6];
export const carOpacity: ExpressionSpecification = ['case', ['get', 'stale'], 0.35, 1];
export const carStrokeWidth: ExpressionSpecification = ['case', ['get', 'selected'], 3, 1.5];

export interface CarFeatureProperties {
  id: string;
  status: CarStatus;
  selected: boolean;
  stale: boolean;
}

/** Cars with a known position; a car without telemetry is only in the list. */
export function carFeatures(
  cars: readonly FleetCar[],
  selectedId: string | null,
  isStale: (car: FleetCar) => boolean,
): GeoJSON.FeatureCollection<GeoJSON.Point, CarFeatureProperties> {
  return {
    type: 'FeatureCollection',
    features: cars.flatMap((car) =>
      car.state
        ? [
            {
              type: 'Feature' as const,
              id: car.id,
              properties: {
                id: car.id,
                status: car.status,
                selected: car.id === selectedId,
                stale: isStale(car),
              },
              geometry: { type: 'Point' as const, coordinates: [car.state.lon, car.state.lat] },
            },
          ]
        : [],
    ),
  };
}
