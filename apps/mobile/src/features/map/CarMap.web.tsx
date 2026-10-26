import 'maplibre-gl/dist/maplibre-gl.css';

import {
  GeolocateControl,
  Map as MapLibreMap,
  type GeoJSONSource,
  type MapGeoJSONFeature,
} from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';

import type { CarMapProps } from './carMapProps';
import { cityZoom, mapStyleUrl, minskCenter, nearbyZoom } from './mapConfig';
import {
  areaOfBounds,
  carFeatures,
  carsLayerId,
  carsSourceId,
  clusterMaxZoom,
  clusterRadius,
  clustersLayerId,
  mapLayers,
  pressedCarId,
  pressedClusterId,
  zoneFeatures,
  zonesSourceId,
} from './mapFeatures';
import './mapWorker';

const fill = { position: 'absolute', inset: 0 } as const;

/**
 * The web map (maplibre-gl, as in the admin panel). Draws the same sources and layers as the
 * native CarMap.tsx; tests replace it, since jsdom has no WebGL.
 */
export default function CarMap({
  cars,
  zones,
  selectedId,
  focus,
  showUserLocation,
  palette,
  label,
  onAreaChange,
  onCarPress,
  onMapPress,
}: CarMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const camera = useRef<{ center: [number, number]; zoom: number }>({
    center: [minskCenter.lon, minskCenter.lat],
    zoom: cityZoom,
  });
  // The map's listeners are set once; they read the latest callbacks from here.
  const latest = useRef({ onAreaChange, onCarPress, onMapPress });
  useEffect(() => {
    latest.current = { onAreaChange, onCarPress, onMapPress };
  });

  useEffect(() => {
    const host = container.current;
    if (!host) return;
    const created = new MapLibreMap({
      container: host,
      style: mapStyleUrl(),
      center: camera.current.center,
      zoom: camera.current.zoom,
      pitchWithRotate: false,
    });
    const reportArea = () => {
      const bounds = created.getBounds();
      const area = areaOfBounds([
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ]);
      if (area !== null) latest.current.onAreaChange(area);
    };

    created.on('load', () => {
      const layers = mapLayers(palette);
      created.addSource(zonesSourceId, { type: 'geojson', data: zoneFeatures([]) });
      created.addSource(carsSourceId, {
        type: 'geojson',
        data: carFeatures([], null),
        cluster: true,
        clusterRadius,
        clusterMaxZoom,
      });
      created.addLayer({ ...layers.zonesFill, source: zonesSourceId });
      created.addLayer({ ...layers.zonesOutline, source: zonesSourceId });
      created.addLayer({ ...layers.clusters, source: carsSourceId });
      created.addLayer({ ...layers.clusterCount, source: carsSourceId });
      created.addLayer({ ...layers.cars, source: carsSourceId });
      setMap(created);
      reportArea();
    });
    created.on('moveend', reportArea);
    created.on('click', (event) => {
      const [hit]: MapGeoJSONFeature[] = created.queryRenderedFeatures(event.point, {
        layers: [carsLayerId, clustersLayerId],
      });
      const carId = pressedCarId(hit);
      if (carId !== null) {
        latest.current.onCarPress(carId);
        return;
      }
      const clusterId = pressedClusterId(hit);
      if (clusterId === null || hit?.geometry.type !== 'Point') {
        latest.current.onMapPress();
        return;
      }
      const [lon, lat] = hit.geometry.coordinates;
      const source = created.getSource<GeoJSONSource>(carsSourceId);
      if (lon === undefined || lat === undefined || !source) return;
      void source.getClusterExpansionZoom(clusterId).then((zoom) => {
        created.easeTo({ center: [lon, lat], zoom });
      });
    });

    return () => {
      // A theme change makes a new map with the new colors; it opens where the old one was.
      const center = created.getCenter();
      camera.current = { center: [center.lng, center.lat], zoom: created.getZoom() };
      created.remove();
      setMap(null);
    };
  }, [palette]);

  useEffect(() => {
    void map?.getSource<GeoJSONSource>(carsSourceId)?.setData(carFeatures(cars, selectedId));
  }, [map, cars, selectedId]);

  useEffect(() => {
    void map?.getSource<GeoJSONSource>(zonesSourceId)?.setData(zoneFeatures(zones));
  }, [map, zones]);

  useEffect(() => {
    if (!map || focus === null) return;
    map.flyTo({ center: [focus.lon, focus.lat], zoom: nearbyZoom });
  }, [map, focus]);

  // The browser's own dot for the client's location; the app already asked for the permission.
  useEffect(() => {
    if (!map || !showUserLocation) return;
    const control = new GeolocateControl({ trackUserLocation: true, showAccuracyCircle: true });
    map.addControl(control);
    return () => {
      map.removeControl(control);
    };
  }, [map, showUserLocation]);

  // A plain div, not a View: MapLibre needs a DOM element of its own to draw into.
  return <div ref={container} style={fill} role="img" aria-label={label} />;
}
