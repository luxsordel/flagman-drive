import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { json, problem, sentRequest } from '../test/http';
import { auditPageSize, createAuditLogApi } from './api';

function auditLogApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createAuditLogApi(client), fetch };
}

describe('audit log API', () => {
  it('sends the filter with whole Minsk days and the access token', async () => {
    const { api, fetch } = auditLogApi(json(200, { items: [], next_cursor: null }));

    await api.list(
      {
        actorId: '0199a7c2-5d1e-7b3a-9f00-000000000001',
        action: 'staff.sign_in_failed',
        from: '2026-10-01',
        to: '2026-10-23',
      },
      'cursor-2',
    );

    const request = sentRequest(fetch);
    const url = new URL(request.url);
    expect(url.pathname).toBe('/admin/audit-events');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      limit: String(auditPageSize),
      actor_id: '0199a7c2-5d1e-7b3a-9f00-000000000001',
      action: 'staff.sign_in_failed',
      occurred_from: '2026-10-01T00:00:00+03:00',
      // Exclusive on the API side, so the last day is included up to its end.
      occurred_to: '2026-10-24T00:00:00+03:00',
      cursor: 'cursor-2',
    });
    expect(request.headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('sends only the page size without a filter', async () => {
    const { api, fetch } = auditLogApi(json(200, { items: [], next_cursor: null }));

    await api.list({});

    expect(Object.fromEntries(new URL(sentRequest(fetch).url).searchParams)).toEqual({
      limit: String(auditPageSize),
    });
  });

  it('returns the page', async () => {
    const page = { items: [], next_cursor: 'cursor-2' };
    const { api } = auditLogApi(json(200, page));

    await expect(api.list({})).resolves.toEqual(page);
  });

  it('rejects when the API refuses', async () => {
    const { api } = auditLogApi(problem(403, 'forbidden'));

    const error: unknown = await api.list({}).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
    expect((error as ApiError).problem).toBe('forbidden');
  });
});
