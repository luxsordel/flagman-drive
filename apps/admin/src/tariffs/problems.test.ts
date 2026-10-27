import { describe, expect, it } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import { tariffError } from './problems';

describe('tariff errors', () => {
  it.each([
    [new ApiError(409, 'tariff-changed'), 'changed'],
    [new ApiError(409, 'tariff-exists'), 'exists'],
    [new ApiError(404, 'tariff-not-found'), 'notFound'],
    [new ApiError(404, 'car-model-not-found'), 'modelNotFound'],
    [new ApiError(422, 'validation-error'), 'invalid'],
    [new ApiError(403, 'forbidden'), 'forbidden'],
    [new ApiError(401, 'access-token-invalid'), 'signOut'],
    [new ApiError(500), 'failed'],
    [new NetworkError(new TypeError('offline')), 'failed'],
  ])('maps %o to %s', (error, outcome) => {
    expect(tariffError(error)).toBe(outcome);
  });
});
