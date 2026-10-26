import { act } from '@testing-library/react';
import { vi } from 'vitest';

import type { FleetApi, FleetCar, FleetRules } from '../fleet/api';
import type {
  CommandNotice,
  FleetMessage,
  FleetStreamHandlers,
  OpenFleetStream,
  StreamStatus,
} from '../fleet/stream';

let counter = 0;

export const fleetRules: FleetRules = { telemetry_max_age_seconds: 300, low_battery_pct: 20 };

/** An available car with fresh telemetry in the centre of Minsk, unless `overrides` say otherwise. */
export function fleetCar(
  overrides: Partial<Omit<FleetCar, 'state'>> & { state?: Partial<FleetCar['state']> | null } = {},
): FleetCar {
  counter += 1;
  const { state, ...rest } = overrides;
  return {
    id: `0199c000-3333-7000-8000-${String(counter).padStart(12, '0')}`,
    plate: `${String(1000 + counter)} AB-7`,
    vin: `LB37622Z9NX${String(counter).padStart(6, '0')}`,
    brand: 'Geely',
    model: 'Geometry C',
    status: 'available',
    state:
      state === null
        ? null
        : {
            lat: 53.9023,
            lon: 27.5615,
            battery_pct: 80,
            range_km: 320,
            doors_locked: true,
            ignition_on: false,
            engine_locked: false,
            recorded_at: new Date().toISOString(),
            ...state,
          },
    ...rest,
  };
}

export const snapshot = (
  cars: FleetCar[],
  rules: FleetRules = fleetRules,
): Extract<FleetMessage, { type: 'snapshot' }> => ({
  type: 'snapshot',
  items: cars,
  rules,
  as_of: new Date().toISOString(),
});

export const carsChanged = (cars: FleetCar[]): Extract<FleetMessage, { type: 'cars' }> => ({
  type: 'cars',
  items: cars,
  as_of: new Date().toISOString(),
});

export const commandNotice = (
  overrides: Partial<CommandNotice> = {},
): Extract<FleetMessage, { type: 'command' }> => ({
  type: 'command',
  command: {
    command_id: '0199c000-4444-7000-8000-000000000001',
    car_id: '0199c000-3333-7000-8000-000000000001',
    command: 'open',
    outcome: 'done',
    reason: null,
    staff_id: '0199a7c2-5d1e-7b3a-9f00-000000000002',
    at: '2026-10-26T12:00:00Z',
    ...overrides,
  },
});

/** A stream the test drives: `send` and `setStatus` act as the server would. */
export function fakeFleetStream() {
  let handlers: FleetStreamHandlers | null = null;
  const close = vi.fn();
  const open = vi.fn<OpenFleetStream>((given) => {
    handlers = given;
    return { close };
  });
  const current = (): FleetStreamHandlers => {
    if (!handlers) throw new Error('the page has not opened the stream');
    return handlers;
  };
  return {
    open,
    close,
    send(message: FleetMessage) {
      act(() => {
        current().onMessage(message);
      });
    },
    setStatus(status: StreamStatus) {
      act(() => {
        current().onStatus(status);
      });
    },
  };
}

/** Commands that succeed and return the car as it was. */
export function fakeFleetApi() {
  return {
    sendCommand: vi.fn<FleetApi['sendCommand']>((carId) =>
      Promise.resolve({
        command_id: '0199c000-4444-7000-8000-000000000009',
        car: fleetCar({ id: carId }),
      }),
    ),
  } satisfies FleetApi;
}
