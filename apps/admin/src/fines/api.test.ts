import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { fine, fineType } from '../test/fines';
import { json, sentRequest } from '../test/http';
import { createFinesApi } from './api';

function finesApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createFinesApi(client), fetch };
}

describe('fines API', () => {
  it('reads the catalog', async () => {
    const type = fineType();
    const { api, fetch } = finesApi(json(200, { items: [type] }));

    expect(await api.types()).toEqual([type]);
    expect(new URL(sentRequest(fetch).url).pathname).toBe('/admin/fine-types');
  });

  it('saves new terms with the version the form was filled from', async () => {
    const { api, fetch } = finesApi(json(200, fineType()));

    await api.updateType('t-1', 2, { name_ru: 'Курение', name_en: 'Smoking', amount: 12000 });

    const request = sentRequest(fetch);
    expect(request.method).toBe('PUT');
    expect(new URL(request.url).pathname).toBe('/admin/fine-types/t-1');
    expect(await request.json()).toEqual({
      based_on_version: 2,
      name_ru: 'Курение',
      name_en: 'Smoking',
      amount: 12000,
    });
  });

  it('sends the filters of the list, the days as Minsk days', async () => {
    const { api, fetch } = finesApi(json(200, { items: [], next_cursor: null }));

    await api.list(
      { status: 'unpaid', fineTypeId: 't-1', userId: 'u-1', from: '2026-11-01', to: '2026-11-02' },
      'c-1',
    );

    const query = new URL(sentRequest(fetch).url).searchParams;
    expect(query.get('status')).toBe('unpaid');
    expect(query.get('fine_type_id')).toBe('t-1');
    expect(query.get('user_id')).toBe('u-1');
    expect(query.get('created_from')).toBe('2026-11-01T00:00:00+03:00');
    expect(query.get('created_to')).toBe('2026-11-03T00:00:00+03:00');
    expect(query.get('cursor')).toBe('c-1');
  });

  it('charges a fine to a rental', async () => {
    const charged = fine();
    const { api, fetch } = finesApi(json(201, charged));

    expect(await api.charge('r-1', 't-1', 'Smoked', 'k-1')).toEqual(charged);
    const request = sentRequest(fetch);
    expect(request.headers.get('Idempotency-Key')).toBe('k-1');
    expect(await request.json()).toEqual({
      rental_id: 'r-1',
      fine_type_id: 't-1',
      comment: 'Smoked',
    });
  });

  it('uploads a photo as the raw body', async () => {
    const { api, fetch } = finesApi(json(201, fine()));
    const photo = new Blob(['jpeg'], { type: 'image/jpeg' });

    await api.addPhoto('f-1', photo);

    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/admin/support/fines/f-1/photos');
    expect(request.headers.get('Content-Type')).toBe('image/jpeg');
    expect(await request.text()).toBe('jpeg');
  });
});
