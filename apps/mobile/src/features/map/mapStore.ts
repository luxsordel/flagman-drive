import type { MapApi, MapArea, MapCar, MapResult, MapZone } from './mapApi';
import { areaSettleMs, carsPerArea, refreshIntervalMs, type LatLon } from './mapConfig';
import type { MapError } from './mapErrors';

export interface SelectedCar {
  id: string;
  /** From the map at once, then replaced by the fresh card; null once the car is gone. */
  car: MapCar | null;
  loading: boolean;
  error: MapError | null;
}

export interface MapState {
  cars: MapCar[];
  zones: MapZone[];
  /** True once cars of some area have loaded: before that an empty list means "not yet". */
  carsLoaded: boolean;
  loadingCars: boolean;
  /** Why the last load of cars or zones failed; what was shown before stays on the map. */
  error: MapError | null;
  selected: SelectedCar | null;
}

const initialState: MapState = {
  cars: [],
  zones: [],
  carsLoaded: false,
  loadingCars: false,
  error: null,
  selected: null,
};

/**
 * Cars and zones of the client map. Framework-free like ProfileStore, so the refresh rules are
 * unit-tested without a map. The client's location lives here only in memory, for the distance.
 */
export class MapStore {
  private readonly api: MapApi;
  private readonly listeners = new Set<() => void>();
  private state: MapState = initialState;
  private area: MapArea | null = null;
  private location: LatLon | null = null;
  private settleTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshTimer: ReturnType<typeof setInterval> | null = null;
  // Each newer request outdates the answers of older ones: a slow answer for the area the client
  // panned away from must not replace the cars of the area they look at now.
  private carsRequest = 0;
  private zonesRequest = 0;
  private cardRequest = 0;
  private zonesFailed = false;

  constructor(api: MapApi) {
    this.api = api;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getState = (): MapState => this.state;

  /** While the map is on screen: loads the zones and refreshes the cars periodically. */
  start(): void {
    if (this.refreshTimer !== null) return;
    void this.loadZones();
    if (this.area !== null) void this.loadCars();
    this.refreshTimer = setInterval(() => {
      if (this.zonesFailed) void this.loadZones();
      if (this.area !== null) void this.loadCars();
    }, refreshIntervalMs);
  }

  /** The map left the screen or the app went to the background: nothing to refresh for. */
  stop(): void {
    if (this.refreshTimer !== null) clearInterval(this.refreshTimer);
    if (this.settleTimer !== null) clearTimeout(this.settleTimer);
    this.refreshTimer = null;
    this.settleTimer = null;
  }

  /** The camera moved; the cars of the new area load once it stays put for a moment. */
  showArea(area: MapArea): void {
    this.area = area;
    if (this.settleTimer !== null) clearTimeout(this.settleTimer);
    this.settleTimer = setTimeout(() => {
      this.settleTimer = null;
      void this.loadCars();
    }, areaSettleMs);
  }

  /** Null when the client did not allow the location or it is not known yet. */
  setLocation(location: LatLon | null): void {
    this.location = location;
  }

  /** The retry after an error. */
  refresh(): void {
    void this.loadZones();
    if (this.area !== null) void this.loadCars();
  }

  select(id: string): void {
    const request = ++this.cardRequest;
    const car = this.state.cars.find((candidate) => candidate.id === id) ?? null;
    this.setState({ ...this.state, selected: { id, car, loading: true, error: null } });
    void settle(this.api.getCar(id, this.location)).then((result) => {
      if (request !== this.cardRequest) return;
      const selected: SelectedCar = result.ok
        ? { id, car: result.value, loading: false, error: null }
        : {
            id,
            car: result.error.kind === 'carGone' ? null : car,
            loading: false,
            error: result.error,
          };
      // A gone car also leaves the map, without waiting for the next refresh.
      const cars =
        !result.ok && result.error.kind === 'carGone'
          ? this.state.cars.filter((candidate) => candidate.id !== id)
          : this.state.cars;
      this.setState({ ...this.state, cars, selected });
    });
  }

  clearSelection(): void {
    this.cardRequest += 1;
    this.setState({ ...this.state, selected: null });
  }

  /** Forgets everything at sign-out, the location included. */
  reset(): void {
    this.stop();
    this.area = null;
    this.location = null;
    this.carsRequest += 1;
    this.zonesRequest += 1;
    this.cardRequest += 1;
    this.zonesFailed = false;
    this.setState(initialState);
  }

  private async loadCars(): Promise<void> {
    const area = this.area;
    if (area === null) return;
    const request = ++this.carsRequest;
    this.setState({ ...this.state, loadingCars: true });
    const result = await settle(this.api.listCars(area, this.location, carsPerArea));
    if (request !== this.carsRequest) return;
    this.setState(
      result.ok
        ? { ...this.state, cars: result.value, carsLoaded: true, loadingCars: false, error: null }
        : { ...this.state, loadingCars: false, error: result.error },
    );
  }

  private async loadZones(): Promise<void> {
    const request = ++this.zonesRequest;
    const result = await settle(this.api.listZones());
    if (request !== this.zonesRequest) return;
    this.zonesFailed = !result.ok;
    this.setState(
      result.ok
        ? { ...this.state, zones: result.value }
        : { ...this.state, error: this.state.error ?? result.error },
    );
  }

  private setState(state: MapState): void {
    this.state = state;
    for (const listener of this.listeners) listener();
  }
}

// A rejected request would leave `loadingCars` on and the map stuck until sign-out.
function settle<T>(request: Promise<MapResult<T>>): Promise<MapResult<T>> {
  return request.catch((): MapResult<T> => ({ ok: false, error: { kind: 'network' } }));
}
