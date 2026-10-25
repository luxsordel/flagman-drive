import type { Zone, ZoneInput, ZoneKind } from './api';

/** `[lon, lat]`, as GeoJSON and MapLibre have it. */
export type Position = [number, number];

/**
 * The outline while it is edited: vertices only, without the closing point GeoJSON repeats at the
 * end. Counting and checking it that way matches what the admin sees on the map.
 */
export type Ring = Position[];

/** Limits of geo/domain.py; the API checks them too, these only explain them before saving. */
export const maxZoneVertices = 500;
export const minZoneVertices = 3;
export const zoneNameMaxLength = 100;

const samePosition = (a: Position, b: Position): boolean => a[0] === b[0] && a[1] === b[1];

/** The vertices of a closed GeoJSON ring: a zone from the API or a polygon drawn on the map. */
export function openRing(closed: readonly (readonly number[])[]): Ring {
  const ring = closed.map(([lon = 0, lat = 0]): Position => [lon, lat]);
  const first = ring[0];
  const last = ring.at(-1);
  if (ring.length > 1 && first && last && samePosition(first, last)) ring.pop();
  return ring;
}

export const zoneRing = (zone: Zone): Ring => openRing(zone.polygon.coordinates[0] ?? []);

export function zoneInput(name: string, kind: ZoneKind, ring: Ring): ZoneInput {
  const first = ring[0];
  return {
    name: name.trim(),
    kind,
    polygon: { type: 'Polygon', coordinates: [first ? [...ring, first] : []] },
  };
}

export interface ZoneFeatureProperties {
  id: string;
  kind: ZoneKind;
  selected: boolean;
}

/**
 * Zones as MapLibre draws them; the one being edited is left out, the drawing tool shows it.
 * Forbidden zones come last, so they lie on top of the allowed ones they cut pieces out of
 * and a click inside such a piece selects the forbidden zone.
 */
export function zoneFeatures(
  zones: readonly Zone[],
  selectedId: string | null,
  editedId: string | null,
): GeoJSON.FeatureCollection<GeoJSON.Polygon, ZoneFeatureProperties> {
  return {
    type: 'FeatureCollection',
    features: zones
      .filter((zone) => zone.id !== editedId)
      .sort((a, b) => Number(a.kind === 'forbidden') - Number(b.kind === 'forbidden'))
      .map((zone) => ({
        type: 'Feature',
        id: zone.id,
        properties: { id: zone.id, kind: zone.kind, selected: zone.id === selectedId },
        geometry: zone.polygon,
      })),
  };
}

/** `[[west, south], [east, north]]` around every vertex, or null when there are none. */
export function ringsBounds(rings: readonly Ring[]): [Position, Position] | null {
  const points = rings.flat();
  if (points.length === 0) return null;
  const lons = points.map(([lon]) => lon);
  const lats = points.map(([, lat]) => lat);
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
}
