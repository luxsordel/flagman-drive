import 'maplibre-gl/dist/maplibre-gl.css';
import '../map/worker';

import { themes, type ThemeName } from '@flagman/design-tokens';
import {
  Map as MapLibreMap,
  NavigationControl,
  type GeoJSONSource,
  type MapMouseEvent,
} from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';
import {
  TerraDraw,
  TerraDrawPolygonMode,
  TerraDrawSelectMode,
  ValidateNotSelfIntersecting,
} from 'terra-draw';
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter';

import { cityZoom, mapStyleUrl, minskCenter } from '../map/config';
import { currentTheme } from '../theme';
import type { Zone, ZoneKind } from './api';
import { openRing, ringsBounds, zoneFeatures, zoneRing, type Ring } from './geometry';
import {
  hexColor,
  zoneColor,
  zoneColorExpression,
  zoneFillOpacity,
  zoneOutlineWidth,
  zonesFillLayerId,
  zonesOutlineLayerId,
  zonesSourceId,
} from './mapStyle';

export interface ZoneMapEditing {
  /** Null while a new zone is drawn. */
  zoneId: string | null;
  kind: ZoneKind;
  /** The outline the editing starts from; empty for a new zone. Later changes come from the map. */
  ring: Ring;
}

export interface ZoneMapProps {
  zones: readonly Zone[];
  selectedId: string | null;
  /** Set while a zone is drawn or changed: the map then draws it with Terra Draw. */
  editing: ZoneMapEditing | null;
  onSelect: (id: string | null) => void;
  /** After every finished step: the polygon closed, a vertex moved, added or deleted. */
  onRingChange: (ring: Ring) => void;
  label: string;
}

const fitPadding = 48;
const fitMaxZoom = 15;

function useThemeName(): ThemeName {
  const [theme, setTheme] = useState(currentTheme);
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(currentTheme());
    });
    observer.observe(document.documentElement, { attributeFilter: ['data-theme'] });
    return () => {
      observer.disconnect();
    };
  }, []);
  return theme;
}

/**
 * The zones on MapLibre with OpenStreetMap tiles; Terra Draw draws and changes one zone at a time.
 * Kept thin on purpose: the page logic lives in editorState.ts, and tests replace this component,
 * since jsdom has no WebGL.
 */
export function ZoneMap({
  zones,
  selectedId,
  editing,
  onSelect,
  onRingChange,
  label,
}: ZoneMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const [draw, setDraw] = useState<TerraDraw | null>(null);
  const palette = themes[useThemeName()];
  // Props change on every render of the page; the map's listeners and effects read the latest.
  const latest = useRef({ onSelect, onRingChange, editing });
  useEffect(() => {
    latest.current = { onSelect, onRingChange, editing };
  });

  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const created = new MapLibreMap({
      container: host,
      style: mapStyleUrl(),
      center: minskCenter,
      zoom: cityZoom,
    });
    created.addControl(new NavigationControl({ showCompass: false }));
    let terraDraw: TerraDraw | null = null;

    created.on('load', () => {
      created.addSource(zonesSourceId, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
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

      terraDraw = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map: created }),
        modes: [
          // A crossed outline cannot be closed: once drawn, it could not be untangled either,
          // since dragging a vertex keeps the edges from crossing.
          new TerraDrawPolygonMode({
            validation: (feature) => ValidateNotSelfIntersecting(feature),
          }),
          new TerraDrawSelectMode({
            // The zone being edited stays selected: Delete would drop it and a click away would
            // hide its vertices, and neither ends the editing.
            allowManualDeselection: false,
            keyEvents: { deselect: null, delete: null, rotate: null, scale: null },
            flags: {
              polygon: {
                feature: {
                  // The whole zone stays where it is; only its vertices move.
                  draggable: false,
                  // The API refuses crossed edges anyway; this stops them while dragging.
                  selfIntersectable: false,
                  coordinates: { draggable: true, midpoints: true, deletable: true },
                },
              },
            },
          }),
        ],
      });
      terraDraw.start();
      const started = terraDraw;
      started.on('finish', (id) => {
        const feature = started.getSnapshotFeature(id);
        if (feature?.geometry.type !== 'Polygon') return;
        latest.current.onRingChange(openRing(feature.geometry.coordinates[0] ?? []));
        // One zone is one polygon: once it is closed, only its vertices can be changed.
        if (started.getMode() === 'polygon') {
          started.setMode('select');
          started.selectFeature(id);
        }
      });
      setDraw(started);
      setMap(created);
    });

    created.on('click', (event: MapMouseEvent) => {
      if (terraDraw && terraDraw.getMode() !== 'static') return;
      const [hit] = created.queryRenderedFeatures(event.point, { layers: [zonesFillLayerId] });
      const id: unknown = hit?.properties.id;
      latest.current.onSelect(typeof id === 'string' ? id : null);
    });

    return () => {
      terraDraw?.stop();
      created.remove();
      setDraw(null);
      setMap(null);
    };
  }, []);

  useEffect(() => {
    if (!map) return;
    // Colors follow the theme; opacity and width are set once with the layers.
    map.setPaintProperty(zonesFillLayerId, 'fill-color', zoneColorExpression(palette));
    map.setPaintProperty(zonesOutlineLayerId, 'line-color', zoneColorExpression(palette));
  }, [map, palette]);

  const editedId = editing?.zoneId ?? null;
  useEffect(() => {
    const source = map?.getSource<GeoJSONSource>(zonesSourceId);
    // The promise only says when the map has redrawn; nothing waits for that.
    if (source) void source.setData(zoneFeatures(zones, selectedId, editedId));
  }, [map, zones, selectedId, editedId]);

  // The first zones that arrive frame the map; later changes leave the view where the admin put it.
  const framed = useRef(false);
  useEffect(() => {
    if (!map || framed.current || zones.length === 0) return;
    const bounds = ringsBounds(zones.map(zoneRing));
    if (!bounds) return;
    framed.current = true;
    map.fitBounds(bounds, { padding: fitPadding, maxZoom: fitMaxZoom, animate: false });
  }, [map, zones]);

  // A zone picked in the list comes into view.
  useEffect(() => {
    const zone = zones.find((candidate) => candidate.id === selectedId);
    const bounds = zone ? ringsBounds([zoneRing(zone)]) : null;
    if (map && bounds) map.fitBounds(bounds, { padding: fitPadding, maxZoom: fitMaxZoom });
    // Only a new selection moves the map, not a saved change of the selected zone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, selectedId]);

  // Starts drawing or changing a zone, and puts the drawing away when it ends. Only the start
  // matters here: while editing, the outline lives in Terra Draw and reaches the page by onRingChange.
  const editingKey = editing ? (editing.zoneId ?? 'new') : null;
  useEffect(() => {
    if (!draw) return;
    draw.clear();
    if (editingKey === null) {
      draw.setMode('static');
      return;
    }
    const ring = latest.current.editing?.ring ?? [];
    const first = ring[0];
    if (!first) {
      draw.setMode('polygon');
      return;
    }
    const id = crypto.randomUUID();
    draw.addFeatures([
      {
        id,
        type: 'Feature',
        properties: { mode: 'polygon' },
        geometry: { type: 'Polygon', coordinates: [[...ring, first]] },
      },
    ]);
    draw.setMode('select');
    draw.selectFeature(id);
  }, [draw, editingKey]);

  // The drawn zone takes the color of the kind chosen for it.
  const editedKind = editing?.kind;
  useEffect(() => {
    if (!draw || !editedKind) return;
    const color = hexColor(zoneColor(editedKind, palette));
    draw.updateModeOptions<typeof TerraDrawPolygonMode>('polygon', {
      styles: { fillColor: color, outlineColor: color },
    });
    draw.updateModeOptions<typeof TerraDrawSelectMode>('select', {
      styles: {
        selectedPolygonColor: color,
        selectedPolygonOutlineColor: color,
        selectionPointColor: color,
        midPointColor: color,
      },
    });
  }, [draw, editedKind, palette]);

  return <div ref={container} className="zone-map" role="region" aria-label={label} />;
}
