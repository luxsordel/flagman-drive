import 'maplibre-gl/dist/maplibre-gl.css';
import '../map/worker';

import { themes } from '@flagman/design-tokens';
import {
  Map as MapLibreMap,
  NavigationControl,
  type GeoJSONSource,
  type MapMouseEvent,
} from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';

import { cityZoom, mapStyleUrl, minskCenter } from '../map/config';
import { useThemeName } from '../map/useThemeName';
import type { Zone } from '../zones/api';
import { ringsBounds, zoneFeatures, type Position } from '../zones/geometry';
import {
  zoneColorExpression,
  zoneFillOpacity,
  zoneOutlineWidth,
  zonesFillLayerId,
  zonesOutlineLayerId,
  zonesSourceId,
} from '../zones/mapStyle';
import type { FleetCar } from './api';
import {
  carFeatures,
  carOpacity,
  carRadius,
  carsLayerId,
  carsSourceId,
  carStrokeWidth,
  statusColorExpression,
} from './mapStyle';

export interface FleetMapProps {
  cars: readonly FleetCar[];
  zones: readonly Zone[];
  selectedId: string | null;
  isStale: (car: FleetCar) => boolean;
  onSelect: (id: string | null) => void;
  label: string;
  /** Shown instead of the map in a browser that cannot draw it. */
  unsupportedText: string;
}

/** MapLibre draws with WebGL2 and fails without it; the rest of the page does not need it. */
function canDrawMaps(): boolean {
  return document.createElement('canvas').getContext('webgl2') !== null;
}

const fitPadding = 48;
const fitMaxZoom = 15;
const selectedZoom = 15;

/**
 * The fleet on MapLibre over the zones. Kept thin on purpose: the section's logic lives in
 * state.ts, and tests replace this component, since jsdom has no WebGL.
 */
export function FleetMap({
  cars,
  zones,
  selectedId,
  isStale,
  onSelect,
  label,
  unsupportedText,
}: FleetMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const [unsupported] = useState(() => !canDrawMaps());
  const palette = themes[useThemeName()];
  // A car picked in the list comes into view; its later moves do not drag the map along, so
  // the effect below reads where the car is from here instead of depending on it.
  const selected = cars.find((car) => car.id === selectedId);
  const selectedPosition: Position | null = selected?.state
    ? [selected.state.lon, selected.state.lat]
    : null;
  const latest = useRef({ onSelect, selectedPosition });
  useEffect(() => {
    latest.current = { onSelect, selectedPosition };
  });

  useEffect(() => {
    const host = container.current;
    if (!host || unsupported) return;
    const created = new MapLibreMap({
      container: host,
      style: mapStyleUrl(),
      center: minskCenter,
      zoom: cityZoom,
    });
    created.addControl(new NavigationControl({ showCompass: false }));

    created.on('load', () => {
      const empty = { type: 'FeatureCollection' as const, features: [] };
      // Zones first, so the cars lie on top of them.
      created.addSource(zonesSourceId, { type: 'geojson', data: empty });
      created.addLayer({
        id: zonesFillLayerId,
        type: 'fill',
        source: zonesSourceId,
        paint: { 'fill-opacity': zoneFillOpacity },
      });
      created.addLayer({
        id: zonesOutlineLayerId,
        type: 'line',
        source: zonesSourceId,
        paint: { 'line-width': zoneOutlineWidth },
      });
      created.addSource(carsSourceId, { type: 'geojson', data: empty });
      created.addLayer({
        id: carsLayerId,
        type: 'circle',
        source: carsSourceId,
        paint: {
          'circle-radius': carRadius,
          'circle-opacity': carOpacity,
          'circle-stroke-opacity': carOpacity,
          'circle-stroke-width': carStrokeWidth,
        },
      });
      setMap(created);
    });

    created.on('click', (event: MapMouseEvent) => {
      const [hit] = created.queryRenderedFeatures(event.point, { layers: [carsLayerId] });
      const id: unknown = hit?.properties.id;
      latest.current.onSelect(typeof id === 'string' ? id : null);
    });

    return () => {
      created.remove();
      setMap(null);
    };
  }, [unsupported]);

  useEffect(() => {
    if (!map) return;
    // Colors follow the theme; sizes are set once with the layers.
    map.setPaintProperty(zonesFillLayerId, 'fill-color', zoneColorExpression(palette));
    map.setPaintProperty(zonesOutlineLayerId, 'line-color', zoneColorExpression(palette));
    map.setPaintProperty(carsLayerId, 'circle-color', statusColorExpression(palette));
    map.setPaintProperty(carsLayerId, 'circle-stroke-color', palette.surface);
  }, [map, palette]);

  useEffect(() => {
    const source = map?.getSource<GeoJSONSource>(zonesSourceId);
    if (source) void source.setData(zoneFeatures(zones, null, null));
  }, [map, zones]);

  useEffect(() => {
    const source = map?.getSource<GeoJSONSource>(carsSourceId);
    if (source) void source.setData(carFeatures(cars, selectedId, isStale));
  }, [map, cars, selectedId, isStale]);

  // The first cars that arrive frame the map; later updates leave the view where staff put it.
  const framed = useRef(false);
  useEffect(() => {
    if (!map || framed.current) return;
    const points = cars.flatMap((car): Position[] =>
      car.state ? [[car.state.lon, car.state.lat]] : [],
    );
    const bounds = ringsBounds([points]);
    if (!bounds) return;
    framed.current = true;
    map.fitBounds(bounds, { padding: fitPadding, maxZoom: fitMaxZoom, animate: false });
  }, [map, cars]);

  useEffect(() => {
    const position = latest.current.selectedPosition;
    if (map && selectedId && position) {
      map.easeTo({ center: position, zoom: Math.max(map.getZoom(), selectedZoom) });
    }
  }, [map, selectedId]);

  return (
    <div ref={container} className="fleet-map" role="region" aria-label={label}>
      {unsupported && <p className="fleet-map-unsupported muted">{unsupportedText}</p>}
    </div>
  );
}
