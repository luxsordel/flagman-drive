import { vi } from 'vitest';

import type { Fine, FinesApi, FineType } from '../fines/api';

let counter = 0;

function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${String(counter).padStart(12, '0')}`;
}

export function fineType(overrides: Partial<FineType> = {}): FineType {
  return {
    id: nextId('0199c000-6666-7000-8000'),
    fine_type_id: nextId('0199c000-7777-7000-8000'),
    code: 'smoking',
    kind: 'manual',
    number: 1,
    name_ru: 'Курение в салоне',
    name_en: 'Smoking in the car',
    amount: 10000,
    valid_from: '2026-11-03T09:00:00Z',
    created_by: null,
    ...overrides,
  };
}

export function fine(overrides: Partial<Fine> = {}): Fine {
  return {
    id: nextId('0199c000-8888-7000-8000'),
    rental_id: '0199c000-9999-7000-8000-000000000001',
    fine_type_id: '0199c000-7777-7000-8000-000000000001',
    code: 'smoking',
    name_ru: 'Курение в салоне',
    name_en: 'Smoking in the car',
    amount: 10000,
    automatic: false,
    status: 'paid',
    comment: 'Smoked',
    cancel_reason: null,
    refund_status: null,
    photos: [],
    created_at: '2026-11-03T10:00:00Z',
    cancelled_at: null,
    user_id: '0199c000-aaaa-7000-8000-000000000001',
    first_name: 'Ivan',
    last_name: 'Petrov',
    car_plate: '1234 AB-7',
    created_by: '0199c000-bbbb-7000-8000-000000000001',
    created_by_name: 'Olga',
    cancelled_by: null,
    events: [
      {
        action: 'charged',
        staff_id: null,
        staff_name: 'Olga',
        comment: 'Smoked',
        created_at: '2026-11-03T10:00:00Z',
      },
    ],
    ...overrides,
  };
}

/** Answers with the given catalog and fines; tests reject a call with `mockRejectedValueOnce`. */
export function fakeFinesApi({
  types = [],
  items = [],
}: { types?: FineType[]; items?: Fine[] } = {}) {
  return {
    types: vi.fn<FinesApi['types']>(() => Promise.resolve(types)),
    createType: vi.fn<FinesApi['createType']>((code, terms) =>
      Promise.resolve(fineType({ code, ...terms })),
    ),
    updateType: vi.fn<FinesApi['updateType']>((fineTypeId, basedOn, terms) =>
      Promise.resolve(fineType({ fine_type_id: fineTypeId, number: basedOn + 1, ...terms })),
    ),
    list: vi.fn<FinesApi['list']>(() => Promise.resolve({ items, next_cursor: null })),
    charge: vi.fn<FinesApi['charge']>((rentalId, fineTypeId, comment) =>
      Promise.resolve(fine({ rental_id: rentalId, fine_type_id: fineTypeId, comment })),
    ),
    cancel: vi.fn<FinesApi['cancel']>((fineId, reason) =>
      Promise.resolve(fine({ id: fineId, status: 'cancelled', cancel_reason: reason })),
    ),
    addPhoto: vi.fn<FinesApi['addPhoto']>((fineId) =>
      Promise.resolve(
        fine({ id: fineId, photos: [{ id: 'photo-1', uploaded_at: '2026-11-03T10:01:00Z' }] }),
      ),
    ),
    photoUrl: vi.fn<FinesApi['photoUrl']>(() =>
      Promise.resolve({ url: 'https://storage.test/fines/1.jpg', expires_in: 300 }),
    ),
  } satisfies FinesApi;
}
