import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { json, sentRequest } from '../test/http';
import { createSupportApi, type IncidentFilter } from './api';

async function incidentsQuery(filter: IncidentFilter) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() =>
    Promise.resolve(json(200, { items: [], next_cursor: null })),
  );
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });

  await createSupportApi(client).incidents(filter);

  return new URL(sentRequest(fetch).url).searchParams.getAll('status');
}

describe('support API', () => {
  it('asks for the open incidents by default: only those come as a queue', async () => {
    await expect(incidentsQuery({})).resolves.toEqual(['new', 'in_progress']);
  });

  it('asks for every incident or for one status when told to', async () => {
    await expect(incidentsQuery({ status: 'all' })).resolves.toEqual([]);
    await expect(incidentsQuery({ status: 'resolved' })).resolves.toEqual(['resolved']);
  });
});
