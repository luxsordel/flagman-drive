import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { json, problem, sentRequest } from '../test/http';
import { demoPrices, modelTariff, tariffVersion } from '../test/tariffs';
import { createTariffsApi } from './api';

function tariffsApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createTariffsApi(client), fetch };
}

describe('tariffs API', () => {
  it('lists the tariffs of every model with the access token', async () => {
    const item = modelTariff('Dolphin');
    const { api, fetch } = tariffsApi(json(200, { items: [item] }));

    expect(await api.list()).toEqual([item]);

    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/admin/tariffs');
    expect(request.headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('lists the versions of a tariff', async () => {
    const version = tariffVersion();
    const { api, fetch } = tariffsApi(json(200, { items: [version] }));

    expect(await api.versions('t-1')).toEqual([version]);
    expect(new URL(sentRequest(fetch).url).pathname).toBe('/admin/tariffs/t-1/versions');
  });

  it('creates a tariff for a model', async () => {
    const item = modelTariff('Dolphin');
    const { api, fetch } = tariffsApi(json(201, item));

    expect(await api.create('m-1', demoPrices)).toEqual(item);

    const request = sentRequest(fetch);
    expect(request.method).toBe('POST');
    expect(await request.json()).toEqual({ model_id: 'm-1', ...demoPrices });
  });

  it('saves a new version based on the one the form was filled from', async () => {
    const item = modelTariff('Dolphin');
    const { api, fetch } = tariffsApi(json(200, item));

    await api.update('t-1', 3, demoPrices);

    const request = sentRequest(fetch);
    expect(request.method).toBe('PUT');
    expect(new URL(request.url).pathname).toBe('/admin/tariffs/t-1');
    expect(await request.json()).toEqual({ based_on_version: 3, ...demoPrices });
  });

  it('rejects with the problem of the API', async () => {
    const { api } = tariffsApi(problem(409, 'tariff-changed'));

    await expect(api.update('t-1', 1, demoPrices)).rejects.toEqual(
      new ApiError(409, 'tariff-changed'),
    );
  });
});
