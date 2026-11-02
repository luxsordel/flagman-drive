import 'maplibre-gl/dist/maplibre-gl.css';
import '../map/worker';

import { Map as MapLibreMap, Marker, NavigationControl } from 'maplibre-gl';
import { useEffect, useRef } from 'react';

import { mapStyleUrl } from '../map/config';

const incidentZoom = 15;

export interface IncidentMapProps {
  lat: number;
  lon: number;
  label: string;
}

/** Where the client reported from. Tests replace this component: jsdom has no WebGL. */
export function IncidentMap({ lat, lon, label }: IncidentMapProps) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = container.current;
    if (!host) return;
    let map: MapLibreMap;
    try {
      map = new MapLibreMap({
        container: host,
        style: mapStyleUrl(),
        center: [lon, lat],
        zoom: incidentZoom,
      });
    } catch {
      // Without WebGL the card still shows the coordinates as text.
      return;
    }
    map.addControl(new NavigationControl({ showCompass: false }));
    new Marker().setLngLat([lon, lat]).addTo(map);
    return () => {
      map.remove();
    };
  }, [lat, lon]);

  return <div ref={container} className="incident-map" role="img" aria-label={label} />;
}
