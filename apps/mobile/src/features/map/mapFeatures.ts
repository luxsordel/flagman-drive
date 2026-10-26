import { fontSize, type Palette } from '@flagman/design-tokens';
import type {
  CircleLayerSpecification,
  FillLayerSpecification,
  LineLayerSpecification,
  SymbolLayerSpecification,
} from '@maplibre/maplibre-react-native';

import type { MapArea, MapCar, MapZone } from './mapApi';

/** Shared by the native map and the web one, so both draw the same thing. */

export const carsSourceId = 'cars';
export const zonesSourceId = 'zones';
export const clustersLayerId = 'cars-clusters';
export const clusterCountLayerId = 'cars-cluster-count';
export const carsLayerId = 'cars-points';
export const zonesFillLayerId = 'zones-fill';
export const zonesOutlineLayerId = 'zones-outline';

/** Markers closer than this on screen merge into a cluster, until the zoom where streets show. */
export const clusterRadius = 48;
export const clusterMaxZoom = 14;

// Glyphs of the default OpenFreeMap style; a custom style without them shows clusters unlabeled.
const clusterFont = ['Noto Sans Bold'];

export type CarFeature = GeoJSON.Feature<GeoJSON.Point, { id: string; selected: boolean }>;
export type ZoneFeature = GeoJSON.Feature<GeoJSON.Polygon, { kind: MapZone['kind'] }>;

export function carFeatures(
  cars: readonly MapCar[],
  selectedId: string | null,
): GeoJSON.FeatureCollection<GeoJSON.Point, CarFeature['properties']> {
  return {
    type: 'FeatureCollection',
    features: cars.map((car) => ({
      type: 'Feature',
      id: car.id,
      geometry: { type: 'Point', coordinates: [car.lon, car.lat] },
      properties: { id: car.id, selected: car.id === selectedId },
    })),
  };
}

export function zoneFeatures(
  zones: readonly MapZone[],
): GeoJSON.FeatureCollection<GeoJSON.Polygon, ZoneFeature['properties']> {
  return {
    type: 'FeatureCollection',
    features: zones.map((zone) => ({
      type: 'Feature',
      id: zone.id,
      geometry: { type: 'Polygon', coordinates: zone.polygon.coordinates },
      properties: { kind: zone.kind },
    })),
  };
}

/** The car id of a pressed marker; null for a cluster or anything else. */
export function pressedCarId(feature: GeoJSON.Feature | undefined): string | null {
  const id: unknown = feature?.properties?.id;
  return typeof id === 'string' && feature?.properties?.cluster !== true ? id : null;
}

/** The cluster id of a pressed cluster; null for a single car or anything else. */
export function pressedClusterId(feature: GeoJSON.Feature | undefined): number | null {
  const id: unknown = feature?.properties?.cluster_id;
  return feature?.properties?.cluster === true && typeof id === 'number' ? id : null;
}

/** `[west, south, east, north]` as MapLibre gives the visible bounds on both platforms. */
export function areaOfBounds([west, south, east, north]: readonly number[]): MapArea | null {
  if (west === undefined || south === undefined || east === undefined || north === undefined) {
    return null;
  }
  // The API takes real coordinates only; a zoomed-out map may report bounds beyond them.
  const area = {
    minLat: Math.max(south, -90),
    minLon: Math.max(west, -180),
    maxLat: Math.min(north, 90),
    maxLon: Math.min(east, 180),
  };
  return area.minLat < area.maxLat && area.minLon < area.maxLon ? area : null;
}

type Layer<T> = Omit<T, 'source'>;
// The same type for fill and line colors, so one expression serves both.
type ColorValue = NonNullable<FillLayerSpecification['paint']>['fill-color'];

export interface MapLayers {
  zonesFill: Layer<FillLayerSpecification>;
  zonesOutline: Layer<LineLayerSpecification>;
  clusters: Layer<CircleLayerSpecification>;
  clusterCount: Layer<SymbolLayerSpecification>;
  cars: Layer<CircleLayerSpecification>;
}

/** Ending a rental is allowed in green zones and not in red ones, as in the admin zone editor. */
export function mapLayers(palette: Palette): MapLayers {
  const zoneColor: ColorValue = [
    'match',
    ['get', 'kind'],
    'forbidden',
    palette.danger,
    palette.success,
  ];
  return {
    zonesFill: {
      id: zonesFillLayerId,
      type: 'fill',
      paint: { 'fill-color': zoneColor, 'fill-opacity': 0.15 },
    },
    zonesOutline: {
      id: zonesOutlineLayerId,
      type: 'line',
      paint: { 'line-color': zoneColor, 'line-width': 1.5 },
    },
    clusters: {
      id: clustersLayerId,
      type: 'circle',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': palette.primary,
        'circle-radius': ['step', ['get', 'point_count'], 16, 10, 20, 50, 26],
        'circle-stroke-color': palette.surface,
        'circle-stroke-width': 2,
      },
    },
    clusterCount: {
      id: clusterCountLayerId,
      type: 'symbol',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': ['get', 'point_count_abbreviated'],
        'text-font': clusterFont,
        'text-size': fontSize.xs,
        'text-allow-overlap': true,
      },
      paint: { 'text-color': palette.onPrimary },
    },
    cars: {
      id: carsLayerId,
      type: 'circle',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': palette.primary,
        'circle-radius': ['case', ['get', 'selected'], 12, 9],
        'circle-stroke-color': [
          'case',
          ['get', 'selected'],
          palette.primaryStrong,
          palette.surface,
        ],
        'circle-stroke-width': ['case', ['get', 'selected'], 4, 2],
      },
    },
  };
}
