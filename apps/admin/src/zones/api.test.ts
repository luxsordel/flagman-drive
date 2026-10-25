import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { json, problem, sentRequest } from '../test/http';
import { zone } from '../test/zones';
import { createZonesApi, type ZoneInput } from './api';

function zonesApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createZonesApi(client), fetch };
}

const input: ZoneInput = {
  name: 'Центр',
  kind: 'allowed',
  polygon: {
    type: 'Polygon',
    coordinates: [
      [
        [27.55, 53.89],
        [27.57, 53.89],
        [27.57, 53.91],
        [27.55, 53.89],
      ],
    ],
  },
};

describe('zones API', () => {
  it('lists the active zones with the access token', async () => {
    const saved = zone();
    const { api, fetch } = zonesApi(json(200, { items: [saved] }));

    expect(await api.list()).toEqual([saved]);

    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/admin/zones');
    expect(new URL(request.url).search).toBe('');
    expect(request.headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('creates a zone', async () => {
    const { api, fetch } = zonesApi(json(201, zone({ ...input })));

    await api.create(input);

    const request = sentRequest(fetch);
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe('/admin/zones');
    expect(await request.json()).toEqual(input);
  });

  it('replaces a zone', async () => {
    const { api, fetch } = zonesApi(json(200, zone({ ...input, id: 'zone-1' })));

    await api.update('zone-1', input);

    const request = sentRequest(fetch);
    expect(request.method).toBe('PUT');
    expect(new URL(request.url).pathname).toBe('/admin/zones/zone-1');
    expect(await request.json()).toEqual(input);
  });

  it('disables a zone instead of deleting it', async () => {
    const { api, fetch } = zonesApi(json(200, zone({ disabled_at: '2026-10-26T10:00:00Z' })));

    await api.disable('zone-1');

    const request = sentRequest(fetch);
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe('/admin/zones/zone-1/disable');
  });

  it('rejects with the broken polygon rule', async () => {
    const { api } = zonesApi(problem(422, 'zone-polygon-self-intersecting'));

    const error: unknown = await api.create(input).catch((failure: unknown) => failure);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, problem: 'zone-polygon-self-intersecting' });
  });
});
