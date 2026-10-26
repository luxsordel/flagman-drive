import { mapNotice } from './mapNotice';
import type { MapState } from './mapStore';
import { mapCar } from './testing';

const state = (overrides: Partial<MapState> = {}): MapState => ({
  cars: [mapCar()],
  zones: [],
  carsLoaded: true,
  loadingCars: false,
  error: null,
  selected: null,
  ...overrides,
});

describe('mapNotice', () => {
  it('says nothing while all is well', () => {
    expect(mapNotice(state(), false)).toBeNull();
  });

  it('puts an error first', () => {
    expect(mapNotice(state({ error: { kind: 'network' }, cars: [] }), true)).toEqual({
      kind: 'error',
      text: 'map.errors.network',
    });
  });

  it('says the first cars are loading, not that there are none', () => {
    expect(mapNotice(state({ cars: [], carsLoaded: false, loadingCars: true }), false)).toEqual({
      kind: 'info',
      text: 'map.loading',
    });
    expect(mapNotice(state({ cars: [], carsLoaded: false }), false)).toBeNull();
  });

  it('explains an empty map', () => {
    expect(mapNotice(state({ cars: [] }), true)).toEqual({ kind: 'info', text: 'map.empty' });
  });

  it('explains the missing distance without the location', () => {
    expect(mapNotice(state(), true)).toEqual({ kind: 'info', text: 'map.locationDenied' });
  });
});
