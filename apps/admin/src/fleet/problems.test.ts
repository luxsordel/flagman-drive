import { describe, expect, it } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import { commandError } from './problems';

describe('command errors', () => {
  it('tells the reason of a car that refused', () => {
    expect(
      commandError(new ApiError(409, 'car-command-rejected', undefined, 'ignition_on')),
    ).toEqual({
      key: 'rejected',
      reason: 'ignition_on',
    });
  });

  it.each([
    [new ApiError(409, 'car-in-active-rental'), 'activeRental'],
    [new ApiError(409, 'car-command-duplicate'), 'duplicate'],
    [new ApiError(503, 'telematics-unavailable'), 'unavailable'],
    [new ApiError(504, 'telematics-timeout'), 'timeout'],
    [new ApiError(404, 'car-not-found'), 'notFound'],
    [new ApiError(422, 'validation-error'), 'invalid'],
    [new ApiError(403, 'forbidden'), 'forbidden'],
    [new ApiError(502), 'unavailable'],
    // A proxy that stopped waiting: the command may still have reached the car.
    [new ApiError(504), 'timeout'],
    [new ApiError(500), 'failed'],
    [new NetworkError(new TypeError('offline')), 'failed'],
  ])('maps %o to %s', (error, key) => {
    expect(commandError(error)).toEqual({ key });
  });

  it('says how long to wait after too many commands', () => {
    expect(commandError(new ApiError(429, 'rate-limited', 30))).toEqual({
      key: 'rateLimited',
      retryAfterSeconds: 30,
    });
  });

  it('signs out on 401', () => {
    expect(commandError(new ApiError(401, 'access-token-invalid'))).toBe('signOut');
  });
});
