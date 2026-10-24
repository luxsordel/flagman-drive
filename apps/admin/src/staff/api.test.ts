import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { json, problem, sentRequest } from '../test/http';
import { createStaffListApi } from './api';

function staffListApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createStaffListApi(client), fetch };
}

describe('staff list API', () => {
  it('returns the staff members with the access token sent', async () => {
    const member = { id: '0199a7c2-5d1e-7b3a-9f00-000000000001', email: 'a@x.by', disabled: false };
    const { api, fetch } = staffListApi(json(200, { items: [member] }));

    await expect(api.list()).resolves.toEqual([member]);

    const request = sentRequest(fetch);
    expect(new URL(request.url).pathname).toBe('/admin/staff');
    expect(request.headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('rejects when the API refuses', async () => {
    const { api } = staffListApi(problem(403, 'forbidden'));

    const error: unknown = await api.list().catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
  });
});
