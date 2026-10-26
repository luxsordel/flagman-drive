import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { fleetCar } from '../test/fleet';
import { json, problem, sentRequest } from '../test/http';
import { canSendCommands, createFleetApi, needsRentalConfirmation } from './api';

function fleetApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createFleetApi(client), fetch };
}

describe('fleet API', () => {
  it('sends a command to the car with the access token', async () => {
    const car = fleetCar();
    const { api, fetch } = fleetApi(json(200, { command_id: 'command-1', car }));

    const result = await api.sendCommand(car.id, {
      command: 'open',
      comment: 'Rental R-1',
      confirm_active_rental: false,
    });

    expect(result.car).toEqual(car);
    const request = sentRequest(fetch);
    expect(request.method).toBe('POST');
    expect(request.url).toBe(`http://admin.test/admin/fleet/cars/${car.id}/commands`);
    expect(request.headers.get('Authorization')).toBe('Bearer access-1');
    expect(await request.json()).toEqual({
      command: 'open',
      comment: 'Rental R-1',
      confirm_active_rental: false,
    });
  });

  it('rejects with the Problem of a refused command', async () => {
    const { api } = fleetApi(problem(409, 'car-command-rejected'));

    await expect(
      api.sendCommand('car-1', { command: 'close', comment: 'x', confirm_active_rental: false }),
    ).rejects.toEqual(new ApiError(409, 'car-command-rejected'));
  });
});

describe('fleet rules of the panel', () => {
  it('lets every staff role send commands, as the API does', () => {
    expect(canSendCommands('operator')).toBe(true);
    expect(canSendCommands('technician')).toBe(true);
    expect(canSendCommands('admin')).toBe(true);
  });

  it('asks for a confirmation only for a car a client holds', () => {
    expect(needsRentalConfirmation('reserved')).toBe(true);
    expect(needsRentalConfirmation('in_rental')).toBe(true);
    expect(needsRentalConfirmation('available')).toBe(false);
    expect(needsRentalConfirmation('service')).toBe(false);
    expect(needsRentalConfirmation('offline')).toBe(false);
  });
});
