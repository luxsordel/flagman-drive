import { describe, expect, it } from 'vitest';

import { zone } from '../test/zones';
import { openRing, ringsBounds, zoneFeatures, zoneInput, zoneRing } from './geometry';

describe('zone geometry', () => {
  it('drops the closing point of a GeoJSON ring', () => {
    expect(
      openRing([
        [27.55, 53.89],
        [27.57, 53.89],
        [27.57, 53.91],
        [27.55, 53.89],
      ]),
    ).toEqual([
      [27.55, 53.89],
      [27.57, 53.89],
      [27.57, 53.91],
    ]);
  });

  it('keeps a ring that is not closed as it is', () => {
    expect(
      openRing([
        [27.55, 53.89],
        [27.57, 53.89],
      ]),
    ).toEqual([
      [27.55, 53.89],
      [27.57, 53.89],
    ]);
    expect(openRing([])).toEqual([]);
  });

  it('closes the ring and trims the name for the API', () => {
    const ring: [number, number][] = [
      [27.55, 53.89],
      [27.57, 53.89],
      [27.57, 53.91],
    ];

    expect(zoneInput('  Центр  ', 'forbidden', ring)).toEqual({
      name: 'Центр',
      kind: 'forbidden',
      polygon: { type: 'Polygon', coordinates: [[...ring, [27.55, 53.89]]] },
    });
  });

  it('round-trips a zone from the API', () => {
    const saved = zone();

    expect(zoneInput(saved.name, saved.kind, zoneRing(saved)).polygon).toEqual(saved.polygon);
  });

  it('draws forbidden zones over allowed ones and leaves out the edited zone', () => {
    const forbidden = zone({ kind: 'forbidden' });
    const allowed = zone();
    const edited = zone();

    const features = zoneFeatures([forbidden, allowed, edited], forbidden.id, edited.id).features;

    expect(features.map((feature) => feature.properties)).toEqual([
      { id: allowed.id, kind: 'allowed', selected: false },
      { id: forbidden.id, kind: 'forbidden', selected: true },
    ]);
  });

  it('frames every vertex', () => {
    expect(
      ringsBounds([
        [
          [27.5, 53.9],
          [27.6, 53.8],
        ],
        [[27.4, 54.0]],
      ]),
    ).toEqual([
      [27.4, 53.8],
      [27.6, 54.0],
    ]);
    expect(ringsBounds([])).toBeNull();
  });
});
