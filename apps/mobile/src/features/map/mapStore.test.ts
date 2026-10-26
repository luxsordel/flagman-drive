import type { MapCar, MapResult } from './mapApi';
import { areaSettleMs, carsPerArea, refreshIntervalMs } from './mapConfig';
import { MapStore } from './mapStore';
import { area, deferred, fakeMapApi, mapCar, mapZone, ok } from './testing';

const failed = <T>(kind: 'network' | 'carGone' | 'rateLimited'): MapResult<T> => ({
  ok: false,
  error: { kind },
});

function setup() {
  const api = fakeMapApi();
  api.listCars.mockResolvedValue(ok([mapCar()]));
  api.listZones.mockResolvedValue(ok([mapZone()]));
  api.getCar.mockResolvedValue(ok(mapCar({ distance_m: 120 })));
  return { api, store: new MapStore(api) };
}

// Lets the fake API's resolved promises reach the store.
const flush = () => jest.advanceTimersByTimeAsync(0);

describe('MapStore', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('loads the zones at start and the cars once the camera stops', async () => {
    const { api, store } = setup();

    store.start();
    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs - 1);
    expect(api.listCars).not.toHaveBeenCalled();
    await jest.advanceTimersByTimeAsync(1);

    expect(api.listZones).toHaveBeenCalledTimes(1);
    expect(api.listCars).toHaveBeenCalledWith(area, null, carsPerArea);
    expect(store.getState()).toMatchObject({
      cars: [mapCar()],
      zones: [mapZone()],
      carsLoaded: true,
      loadingCars: false,
      error: null,
    });
  });

  it('loads only the area where a pan stops', async () => {
    const { api, store } = setup();
    const other = { ...area, minLat: 53.8 };

    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs / 2);
    store.showArea(other);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    expect(api.listCars).toHaveBeenCalledTimes(1);
    expect(api.listCars).toHaveBeenCalledWith(other, null, carsPerArea);
  });

  it('sends the location for the distance once it is known', async () => {
    const { api, store } = setup();

    store.setLocation({ lat: 53.91, lon: 27.55 });
    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    expect(api.listCars).toHaveBeenCalledWith(area, { lat: 53.91, lon: 27.55 }, carsPerArea);
  });

  it('refreshes the cars periodically until stopped', async () => {
    const { api, store } = setup();
    store.showArea(area);
    store.start();
    await flush();
    expect(api.listCars).toHaveBeenCalledTimes(1);

    await jest.advanceTimersByTimeAsync(refreshIntervalMs);
    expect(api.listCars).toHaveBeenCalledTimes(3);

    store.stop();
    await jest.advanceTimersByTimeAsync(refreshIntervalMs * 2);
    expect(api.listCars).toHaveBeenCalledTimes(3);
  });

  it('starts once however often it is asked', async () => {
    const { api, store } = setup();

    store.start();
    store.start();
    await jest.advanceTimersByTimeAsync(refreshIntervalMs);

    expect(api.listZones).toHaveBeenCalledTimes(1);
  });

  it('keeps an outdated answer from replacing the cars of the area shown now', async () => {
    const { api, store } = setup();
    const slow = deferred<MapResult<MapCar[]>>();
    api.listCars
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce(ok([mapCar({ id: 'fresh' })]));

    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);
    store.showArea({ ...area, maxLat: 54 });
    await jest.advanceTimersByTimeAsync(areaSettleMs);
    slow.resolve(ok([mapCar({ id: 'stale' })]));
    await flush();

    expect(store.getState().cars.map((car) => car.id)).toEqual(['fresh']);
  });

  it('keeps the cars on the map when a refresh fails', async () => {
    const { api, store } = setup();
    api.listCars.mockResolvedValueOnce(ok([mapCar()])).mockResolvedValueOnce(failed('network'));

    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);
    store.refresh();
    await flush();

    expect(store.getState()).toMatchObject({
      cars: [mapCar()],
      error: { kind: 'network' },
      loadingCars: false,
    });
  });

  it('treats a rejected request as a network error', async () => {
    const { api, store } = setup();
    api.listCars.mockRejectedValue(new TypeError('Network request failed'));

    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    expect(store.getState()).toMatchObject({ loadingCars: false, error: { kind: 'network' } });
  });

  it('retries the zones with the next refresh after a failure', async () => {
    const { api, store } = setup();
    api.listZones.mockResolvedValueOnce(failed('network'));

    store.start();
    await flush();
    expect(store.getState().error).toEqual({ kind: 'network' });
    await jest.advanceTimersByTimeAsync(refreshIntervalMs);

    expect(api.listZones).toHaveBeenCalledTimes(2);
    expect(store.getState().zones).toEqual([mapZone()]);
  });

  it('shows a picked car at once and then its fresh card', async () => {
    const { api, store } = setup();
    store.setLocation({ lat: 53.9, lon: 27.56 });
    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    store.select('car-1');
    expect(store.getState().selected).toEqual({
      id: 'car-1',
      car: mapCar(),
      loading: true,
      error: null,
    });
    await flush();

    expect(api.getCar).toHaveBeenCalledWith('car-1', { lat: 53.9, lon: 27.56 });
    expect(store.getState().selected).toEqual({
      id: 'car-1',
      car: mapCar({ distance_m: 120 }),
      loading: false,
      error: null,
    });
  });

  it('takes a car that is gone off the map', async () => {
    const { api, store } = setup();
    api.listCars.mockResolvedValue(ok([mapCar(), mapCar({ id: 'car-2' })]));
    api.getCar.mockResolvedValue(failed('carGone'));
    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    store.select('car-1');
    await flush();

    expect(store.getState().selected).toEqual({
      id: 'car-1',
      car: null,
      loading: false,
      error: { kind: 'carGone' },
    });
    expect(store.getState().cars.map((car) => car.id)).toEqual(['car-2']);
  });

  it('keeps the card from the map when the fresh one fails to load', async () => {
    const { api, store } = setup();
    api.getCar.mockResolvedValue(failed('network'));
    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    store.select('car-1');
    await flush();

    expect(store.getState().selected).toMatchObject({ car: mapCar(), error: { kind: 'network' } });
  });

  it('ignores the answer for a card that was closed', async () => {
    const { api, store } = setup();
    const slow = deferred<MapResult<MapCar>>();
    api.getCar.mockReturnValue(slow.promise);

    store.select('car-1');
    store.clearSelection();
    slow.resolve(ok(mapCar()));
    await flush();

    expect(store.getState().selected).toBeNull();
  });

  it('forgets the cars, the location and the timers at sign-out', async () => {
    const { api, store } = setup();
    store.setLocation({ lat: 53.9, lon: 27.56 });
    store.showArea(area);
    store.start();
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    const callsBefore = api.listCars.mock.calls.length;
    store.reset();
    await jest.advanceTimersByTimeAsync(refreshIntervalMs * 2);
    expect(api.listCars).toHaveBeenCalledTimes(callsBefore);
    store.showArea(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);

    expect(api.listCars).toHaveBeenLastCalledWith(area, null, carsPerArea);
    expect(store.getState().selected).toBeNull();
  });
});
