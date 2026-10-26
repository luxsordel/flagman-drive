import { describe, expect, it } from 'vitest';

import { carsChanged, commandNotice, fleetCar, fleetRules, snapshot } from '../test/fleet';
import {
  fleetReducer,
  initialFleetState,
  isLowBattery,
  isTelemetryStale,
  visibleCars,
  type FleetAction,
  type FleetState,
} from './state';
import type { FleetMessage } from './stream';

const received = Date.parse('2026-10-26T12:00:00Z');

const apply = (...actions: FleetAction[]): FleetState =>
  actions.reduce(fleetReducer, initialFleetState);

const message = (value: FleetMessage, receivedAt = received): FleetAction => ({
  type: 'message',
  message: value,
  receivedAt,
});

describe('fleet state', () => {
  it('takes the whole fleet from a snapshot and drops cars that are gone', () => {
    const kept = fleetCar();
    const gone = fleetCar();

    const state = apply(message(snapshot([kept, gone])), message(snapshot([kept])));

    expect(Object.keys(state.cars)).toEqual([kept.id]);
    expect(state.rules).toEqual(fleetRules);
  });

  it('replaces the changed cars and keeps the others', () => {
    const first = fleetCar();
    const second = fleetCar();
    const moved = { ...first, status: 'service' as const };

    const state = apply(message(snapshot([first, second])), message(carsChanged([moved])));

    expect(state.cars[first.id]?.status).toBe('service');
    expect(state.cars[second.id]).toEqual(second);
  });

  it('keeps the last command of each car', () => {
    const state = apply(
      message(commandNotice({ car_id: 'car-1', command: 'open' })),
      message(commandNotice({ car_id: 'car-1', command: 'close', outcome: 'rejected' })),
    );

    expect(state.lastCommands['car-1']).toMatchObject({ command: 'close', outcome: 'rejected' });
  });

  it('counts telemetry age on the server clock', () => {
    const serverNow = '2026-10-26T12:00:00Z';
    const car = fleetCar({ state: { recorded_at: '2026-10-26T11:56:00Z' } });
    // The browser runs ten minutes behind the server.
    const browserNow = Date.parse(serverNow) - 10 * 60_000;
    const state = apply(message({ ...snapshot([car]), as_of: serverNow }, browserNow));

    expect(state.clockOffsetMs).toBe(10 * 60_000);
    // Four minutes old: fresh, though by the browser's clock it is from the future.
    expect(isTelemetryStale(car, state.rules, state.clockOffsetMs, browserNow)).toBe(false);
    expect(isTelemetryStale(car, state.rules, state.clockOffsetMs, browserNow + 2 * 60_000)).toBe(
      true,
    );
  });

  it('takes a car without telemetry, or without rules yet, for stale', () => {
    expect(isTelemetryStale(fleetCar({ state: null }), fleetRules, 0, Date.now())).toBe(true);
    expect(isTelemetryStale(fleetCar(), null, 0, Date.now())).toBe(true);
  });

  it('marks a charge below the rentable one as low', () => {
    expect(isLowBattery(fleetCar({ state: { battery_pct: 19 } }), fleetRules)).toBe(true);
    expect(isLowBattery(fleetCar({ state: { battery_pct: 20 } }), fleetRules)).toBe(false);
    expect(isLowBattery(fleetCar({ state: null }), fleetRules)).toBe(false);
  });

  it('filters by status and low battery, and lists by plate', () => {
    const low = fleetCar({ plate: '0002 AB-7', state: { battery_pct: 5 } });
    const inService = fleetCar({ plate: '0001 AB-7', status: 'service' });
    const offline = fleetCar({ plate: '0003 AB-7', status: 'offline', state: { battery_pct: 5 } });
    const loaded = apply(message(snapshot([low, inService, offline])));

    expect(visibleCars(loaded).map((car) => car.plate)).toEqual([
      '0001 AB-7',
      '0002 AB-7',
      '0003 AB-7',
    ]);
    const byStatus = apply(
      message(snapshot([low, inService, offline])),
      { type: 'toggleStatus', status: 'service' },
      { type: 'toggleStatus', status: 'offline' },
    );
    expect(visibleCars(byStatus)).toEqual([inService, offline]);
    const lowOffline = fleetReducer(byStatus, { type: 'setLowBattery', lowBattery: true });
    expect(visibleCars(lowOffline)).toEqual([offline]);
    const allAgain = fleetReducer(
      fleetReducer(lowOffline, { type: 'toggleStatus', status: 'service' }),
      { type: 'toggleStatus', status: 'offline' },
    );
    expect(visibleCars(allAgain)).toEqual([low, offline]);
  });
});
