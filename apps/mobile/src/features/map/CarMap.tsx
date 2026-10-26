import {
  Camera,
  GeoJSONSource,
  Layer,
  Map,
  UserLocation,
  type CameraRef,
  type GeoJSONSourceRef,
} from '@maplibre/maplibre-react-native';
import { useEffect, useMemo, useRef } from 'react';
import { StyleSheet } from 'react-native';

import type { CarMapProps } from './carMapProps';
import { cityZoom, mapStyleUrl, minskCenter, nearbyZoom } from './mapConfig';
import {
  areaOfBounds,
  carFeatures,
  carsSourceId,
  clusterMaxZoom,
  clusterRadius,
  mapLayers,
  pressedCarId,
  pressedClusterId,
  zoneFeatures,
  zonesSourceId,
} from './mapFeatures';

/**
 * The native map (MapLibre React Native). Kept thin on purpose: the rules live in mapStore.ts
 * and mapFeatures.ts, and tests replace this component, since Jest has no native map.
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
  const camera = useRef<CameraRef>(null);
  const carsSource = useRef<GeoJSONSourceRef>(null);
  const layers = useMemo(() => mapLayers(palette), [palette]);
  const carData = useMemo(() => carFeatures(cars, selectedId), [cars, selectedId]);
  const zoneData = useMemo(() => zoneFeatures(zones), [zones]);

  useEffect(() => {
    if (focus === null) return;
    camera.current?.flyTo({ center: [focus.lon, focus.lat], zoom: nearbyZoom, duration: 800 });
  }, [focus]);

  return (
    <Map
      style={StyleSheet.absoluteFill}
      mapStyle={mapStyleUrl()}
      accessibilityLabel={label}
      compass={false}
      touchPitch={false}
      onPress={onMapPress}
      onRegionDidChange={(event) => {
        const area = areaOfBounds(event.nativeEvent.bounds);
        if (area !== null) onAreaChange(area);
      }}
    >
      <Camera
        ref={camera}
        initialViewState={{ center: [minskCenter.lon, minskCenter.lat], zoom: cityZoom }}
      />
      <GeoJSONSource id={zonesSourceId} data={zoneData}>
        <Layer {...layers.zonesFill} />
        <Layer {...layers.zonesOutline} />
      </GeoJSONSource>
      <GeoJSONSource
        ref={carsSource}
        id={carsSourceId}
        data={carData}
        cluster
        clusterRadius={clusterRadius}
        clusterMaxZoom={clusterMaxZoom}
        onPress={(event) => {
          // A car press must not also reach the map, which would close the card it opens.
          event.stopPropagation();
          const feature = event.nativeEvent.features[0];
          const carId = pressedCarId(feature);
          if (carId !== null) {
            onCarPress(carId);
            return;
          }
          const clusterId = pressedClusterId(feature);
          if (clusterId === null || feature?.geometry.type !== 'Point') return;
          const [lon, lat] = feature.geometry.coordinates;
          if (lon === undefined || lat === undefined) return;
          void carsSource.current?.getClusterExpansionZoom(clusterId).then((zoom) => {
            camera.current?.easeTo({ center: [lon, lat], zoom, duration: 400 });
          });
        }}
      >
        <Layer {...layers.clusters} />
        <Layer {...layers.clusterCount} />
        <Layer {...layers.cars} />
      </GeoJSONSource>
      {showUserLocation ? <UserLocation accuracy /> : null}
    </Map>
  );
}
