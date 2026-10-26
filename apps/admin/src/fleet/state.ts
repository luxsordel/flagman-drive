import type { CarStatus, FleetCar, FleetRules } from './api';
import type { CommandNotice, FleetMessage, StreamStatus } from './stream';

export interface FleetFilter {
  /** None means every status. */
  statuses: readonly CarStatus[];
  lowBattery: boolean;
}

export interface FleetState {
  cars: Readonly<Record<string, FleetCar>>;
  /** Null until the first snapshot. */
  rules: FleetRules | null;
  /** The server's clock minus the browser's: telemetry age is counted on the server's clock. */
  clockOffsetMs: number;
  status: StreamStatus;
  selectedId: string | null;
  filter: FleetFilter;
  /** The last command of every car, by whoever sent it. */
  lastCommands: Readonly<Record<string, CommandNotice>>;
}

export const initialFleetState: FleetState = {
  cars: {},
  rules: null,
  clockOffsetMs: 0,
  status: 'connecting',
  selectedId: null,
  filter: { statuses: [], lowBattery: false },
  lastCommands: {},
};

export type FleetAction =
  | { type: 'message'; message: FleetMessage; receivedAt: number }
  | { type: 'status'; status: StreamStatus }
  | { type: 'select'; id: string | null }
  | { type: 'toggleStatus'; status: CarStatus }
  | { type: 'setLowBattery'; lowBattery: boolean };

const byId = (cars: readonly FleetCar[]): Record<string, FleetCar> =>
  Object.fromEntries(cars.map((car) => [car.id, car]));

function applyMessage(state: FleetState, message: FleetMessage, receivedAt: number): FleetState {
  switch (message.type) {
    case 'snapshot':
      // Whole: a car gone from the fleet is gone from the panel too.
      return {
        ...state,
        cars: byId(message.items),
        rules: message.rules,
        clockOffsetMs: Date.parse(message.as_of) - receivedAt,
      };
    case 'cars':
      return {
        ...state,
        cars: { ...state.cars, ...byId(message.items) },
        clockOffsetMs: Date.parse(message.as_of) - receivedAt,
      };
    case 'command':
      return {
        ...state,
        lastCommands: { ...state.lastCommands, [message.command.car_id]: message.command },
      };
  }
}

export function fleetReducer(state: FleetState, action: FleetAction): FleetState {
  switch (action.type) {
    case 'message':
      return applyMessage(state, action.message, action.receivedAt);
    case 'status':
      return { ...state, status: action.status };
    case 'select':
      return { ...state, selectedId: action.id };
    case 'toggleStatus': {
      const { statuses } = state.filter;
      const next = statuses.includes(action.status)
        ? statuses.filter((status) => status !== action.status)
        : [...statuses, action.status];
      return { ...state, filter: { ...state.filter, statuses: next } };
    }
    case 'setLowBattery':
      return { ...state, filter: { ...state.filter, lowBattery: action.lowBattery } };
  }
}

/** The same rule as fleet.domain.is_low_battery: below the charge cars are rented out at. */
export const isLowBattery = (car: FleetCar, rules: FleetRules): boolean =>
  car.state !== null && car.state.battery_pct < rules.low_battery_pct;

/**
 * Whether the car may be elsewhere by now: no telemetry, or older than the rules allow.
 * `now` is the browser's clock; `clockOffsetMs` of FleetState turns it into the server's.
 */
export function isTelemetryStale(
  car: FleetCar,
  rules: FleetRules | null,
  clockOffsetMs: number,
  now: number,
): boolean {
  if (car.state === null || rules === null) return true;
  const age = now + clockOffsetMs - Date.parse(car.state.recorded_at);
  return age > rules.telemetry_max_age_seconds * 1000;
}

/** The cars the filter lets through, by plate as the API lists them. */
export function visibleCars(state: FleetState): FleetCar[] {
  const { statuses, lowBattery } = state.filter;
  const { rules } = state;
  return Object.values(state.cars)
    .filter((car) => statuses.length === 0 || statuses.includes(car.status))
    .filter((car) => !lowBattery || (rules !== null && isLowBattery(car, rules)))
    .sort((a, b) => a.plate.localeCompare(b.plate));
}
