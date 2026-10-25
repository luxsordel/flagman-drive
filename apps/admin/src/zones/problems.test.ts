import { describe, expect, it } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import { zoneError } from './problems';

describe('zone errors', () => {
  it.each([
    ['zone-polygon-self-intersecting', 'selfIntersecting'],
    ['zone-polygon-too-many-vertices', 'tooManyVertices'],
    ['zone-polygon-too-few-vertices', 'tooFewVertices'],
    ['zone-polygon-outside-area', 'outsideArea'],
    ['zone-polygon-not-closed', 'notClosed'],
    ['zone-polygon-holes', 'holes'],
  ])('names the broken polygon rule %s', (problem, expected) => {
    expect(zoneError(new ApiError(422, problem))).toBe(expected);
  });

  it('tells a gone or disabled zone apart', () => {
    expect(zoneError(new ApiError(404, 'zone-not-found'))).toBe('notFound');
    expect(zoneError(new ApiError(409, 'zone-disabled'))).toBe('disabled');
  });

  it('reports other validation errors without a rule', () => {
    expect(zoneError(new ApiError(422, 'validation-error'))).toBe('invalid');
  });

  it('signs out on 401 and explains 403', () => {
    expect(zoneError(new ApiError(401, 'access-token-invalid'))).toBe('signOut');
    expect(zoneError(new ApiError(403, 'forbidden'))).toBe('forbidden');
  });

  it('offers to try again when the API fails or cannot be reached', () => {
    expect(zoneError(new ApiError(500))).toBe('failed');
    expect(zoneError(new NetworkError(new TypeError('offline')))).toBe('failed');
  });
});
