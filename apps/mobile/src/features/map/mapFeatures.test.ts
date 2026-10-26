import { lightPalette } from '@flagman/design-tokens';

import {
  areaOfBounds,
  carFeatures,
  mapLayers,
  pressedCarId,
  pressedClusterId,
  zoneFeatures,
} from './mapFeatures';
import { mapCar, mapZone } from './testing';

describe('carFeatures', () => {
  it('puts each car at [lon, lat] and marks the selected one', () => {
    const features = carFeatures(
      [mapCar(), mapCar({ id: 'car-2', lat: 53.95, lon: 27.6 })],
      'car-2',
    );

    expect(features.features).toEqual([
      {
        type: 'Feature',
        id: 'car-1',
        geometry: { type: 'Point', coordinates: [27.5615, 53.9023] },
        properties: { id: 'car-1', selected: false },
      },
      {
        type: 'Feature',
        id: 'car-2',
        geometry: { type: 'Point', coordinates: [27.6, 53.95] },
        properties: { id: 'car-2', selected: true },
      },
    ]);
  });
});

describe('zoneFeatures', () => {
  it('keeps the outline and the kind, nothing else', () => {
    const zone = mapZone({ kind: 'forbidden' });

    expect(zoneFeatures([zone]).features).toEqual([
      { type: 'Feature', id: 'zone-1', geometry: zone.polygon, properties: { kind: 'forbidden' } },
    ]);
  });
});

describe('pressed features', () => {
  const point: GeoJSON.Point = { type: 'Point', coordinates: [27.56, 53.9] };
  const car: GeoJSON.Feature = { type: 'Feature', geometry: point, properties: { id: 'car-1' } };
  const cluster: GeoJSON.Feature = {
    type: 'Feature',
    geometry: point,
    properties: { cluster: true, cluster_id: 7, point_count: 12 },
  };

  it('tells a car from a cluster', () => {
    expect(pressedCarId(car)).toBe('car-1');
    expect(pressedClusterId(car)).toBeNull();
    expect(pressedCarId(cluster)).toBeNull();
    expect(pressedClusterId(cluster)).toBe(7);
  });

  it('ignores anything else', () => {
    expect(pressedCarId(undefined)).toBeNull();
    expect(pressedClusterId({ type: 'Feature', geometry: point, properties: null })).toBeNull();
  });
});

describe('areaOfBounds', () => {
  it('turns [west, south, east, north] into an area', () => {
    expect(areaOfBounds([27.4, 53.8, 27.7, 54])).toEqual({
      minLat: 53.8,
      minLon: 27.4,
      maxLat: 54,
      maxLon: 27.7,
    });
  });

  it('clamps a zoomed-out view to real coordinates', () => {
    expect(areaOfBounds([-200, -95, 200, 95])).toEqual({
      minLat: -90,
      minLon: -180,
      maxLat: 90,
      maxLon: 180,
    });
  });

  it('drops an empty or incomplete view', () => {
    expect(areaOfBounds([27.5, 53.9, 27.5, 53.9])).toBeNull();
    expect(areaOfBounds([27.5, 53.9])).toBeNull();
  });
});

describe('mapLayers', () => {
  it('colors allowed zones green and forbidden ones red, from the tokens', () => {
    const { zonesFill, zonesOutline } = mapLayers(lightPalette);
    const color = [
      'match',
      ['get', 'kind'],
      'forbidden',
      lightPalette.danger,
      lightPalette.success,
    ];

    expect(zonesFill.paint?.['fill-color']).toEqual(color);
    expect(zonesOutline.paint?.['line-color']).toEqual(color);
  });

  it('draws cars and clusters in the brand color', () => {
    const { cars, clusters } = mapLayers(lightPalette);

    expect(cars.paint?.['circle-color']).toBe(lightPalette.primary);
    expect(clusters.paint?.['circle-color']).toBe(lightPalette.primary);
  });
});
