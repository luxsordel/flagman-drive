import { createApiClient } from '@flagman/api-client';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { kycApplication } from '../test/kyc';
import { json, problem, sentRequest } from '../test/http';
import { createKycReviewApi, kycQueueSize } from './api';

const applicationId = '0199b000-1111-7000-8000-000000000001';

function kycReviewApi(response: Response) {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(() => Promise.resolve(response));
  const client = createApiClient({
    baseUrl: 'http://admin.test',
    getAccessToken: () => 'access-1',
    fetch,
  });
  return { api: createKycReviewApi(client), fetch };
}

async function sentJson(request: Request): Promise<unknown> {
  return JSON.parse(await request.text()) as unknown;
}

describe('KYC review API', () => {
  it('asks for the whole queue with the access token', async () => {
    const queue = { items: [], total: 0 };
    const { api, fetch } = kycReviewApi(json(200, queue));

    await expect(api.queue()).resolves.toEqual(queue);

    const request = sentRequest(fetch);
    const url = new URL(request.url);
    expect(url.pathname).toBe('/admin/kyc/applications');
    expect(url.searchParams.get('limit')).toBe(String(kycQueueSize));
    expect(request.headers.get('Authorization')).toBe('Bearer access-1');
  });

  it('asks for a shorter queue when only the total is needed', async () => {
    const { api, fetch } = kycReviewApi(json(200, { items: [], total: 3 }));

    await api.queue(1);

    expect(new URL(sentRequest(fetch).url).searchParams.get('limit')).toBe('1');
  });

  it('loads an application by its id', async () => {
    const application = kycApplication({ id: applicationId });
    const { api, fetch } = kycReviewApi(json(200, application));

    await expect(api.application(applicationId)).resolves.toEqual(application);

    expect(new URL(sentRequest(fetch).url).pathname).toBe(
      `/admin/kyc/applications/${applicationId}`,
    );
  });

  it('asks for a link to one document photo', async () => {
    const link = { url: 'http://storage.test/kyc/a.jpg?signature=1', expires_in: 300 };
    const { api, fetch } = kycReviewApi(json(200, link));

    await expect(api.documentUrl(applicationId, 'license_back')).resolves.toEqual(link);

    expect(new URL(sentRequest(fetch).url).pathname).toBe(
      `/admin/kyc/applications/${applicationId}/documents/license_back/url`,
    );
  });

  it('approves with the license issue date', async () => {
    const { api, fetch } = kycReviewApi(json(200, kycApplication({ status: 'approved' })));

    await api.approve(applicationId, '2015-06-01');

    const request = sentRequest(fetch);
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe(`/admin/kyc/applications/${applicationId}/approve`);
    expect(await sentJson(request)).toEqual({ license_issued_on: '2015-06-01' });
  });

  it('rejects with the reason', async () => {
    const { api, fetch } = kycReviewApi(json(200, kycApplication({ status: 'rejected' })));

    await api.reject(applicationId, 'The selfie is blurry');

    const request = sentRequest(fetch);
    expect(request.method).toBe('POST');
    expect(new URL(request.url).pathname).toBe(`/admin/kyc/applications/${applicationId}/reject`);
    expect(await sentJson(request)).toEqual({ reason: 'The selfie is blurry' });
  });

  it('rejects with the problem when the API refuses', async () => {
    const { api } = kycReviewApi(problem(409, 'kyc-not-pending'));

    const error: unknown = await api
      .approve(applicationId, '2015-06-01')
      .catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).problem).toBe('kyc-not-pending');
  });
});
