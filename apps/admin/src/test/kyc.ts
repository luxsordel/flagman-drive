import { vi } from 'vitest';

import type { KycApplication, KycQueue, KycQueueItem, KycReviewApi } from '../kyc/api';

let counter = 0;

const nextId = (): string => {
  counter += 1;
  return `0199b000-1111-7000-8000-${String(counter).padStart(12, '0')}`;
};

export function kycQueueItem(overrides: Partial<KycQueueItem> = {}): KycQueueItem {
  return {
    id: nextId(),
    first_name: 'Ivan',
    last_name: 'Petrov',
    submitted_at: '2026-10-23T09:00:00Z',
    ...overrides,
  };
}

/** A pending application with all four documents. */
export function kycApplication(overrides: Partial<KycApplication> = {}): KycApplication {
  return {
    id: nextId(),
    status: 'pending',
    first_name: 'Ivan',
    last_name: 'Petrov',
    birth_date: '1990-05-17',
    age_years: 36,
    submitted_at: '2026-10-23T09:00:00Z',
    decided_at: null,
    rejection_reason: null,
    license_issued_on: null,
    documents: (['license_front', 'license_back', 'passport', 'selfie_with_license'] as const).map(
      (kind) => ({ kind, uploaded_at: '2026-10-23T08:50:00Z' }),
    ),
    ...overrides,
  };
}

/**
 * The review API with `queue` waiting and `application` open; links name the photo and count
 * how many were given, so a test can tell a fresh link from an old one.
 */
export function fakeKycReviewApi({
  queue = { items: [], total: 0 },
  application = kycApplication(),
}: { queue?: KycQueue; application?: KycApplication } = {}) {
  let links = 0;
  return {
    queue: vi.fn<KycReviewApi['queue']>(() => Promise.resolve(queue)),
    application: vi.fn<KycReviewApi['application']>(() => Promise.resolve(application)),
    documentUrl: vi.fn<KycReviewApi['documentUrl']>((_id, kind) => {
      links += 1;
      return Promise.resolve({
        url: `http://storage.test/${kind}.jpg?link=${String(links)}`,
        expires_in: 300,
      });
    }),
    approve: vi.fn<KycReviewApi['approve']>((_id, licenseIssuedOn) =>
      Promise.resolve({ ...application, status: 'approved', license_issued_on: licenseIssuedOn }),
    ),
    reject: vi.fn<KycReviewApi['reject']>((_id, reason) =>
      Promise.resolve({ ...application, status: 'rejected', rejection_reason: reason }),
    ),
  } satisfies KycReviewApi;
}
