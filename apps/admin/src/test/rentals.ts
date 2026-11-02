import { vi } from 'vitest';

import type { RentalsApi, StaffRental, StaffRentalItem } from '../rentals/api';

let counter = 0;

const nextId = (): string => {
  counter += 1;
  return `0199c000-2222-7000-8000-${String(counter).padStart(12, '0')}`;
};

/** A paid trip of 30 minutes, 12.00 BYN, with its hold and capture. */
export function staffRental(overrides: Partial<StaffRental> = {}): StaffRental {
  const id = nextId();
  const holdId = nextId();
  return {
    rental: {
      id,
      status: 'paid',
      car: {
        id: nextId(),
        brand: 'Geely',
        model: 'Geometry C',
        plate: '1234 AB-7',
        photo_url: null,
        lat: 53.9,
        lon: 27.56,
        battery_pct: 80,
        range_km: 300,
        doors_locked: true,
      },
      tariff: { driving_per_minute: 39, parked_per_minute: 9, waiting_per_minute: 9 },
      reserved_at: '2026-11-02T09:00:00Z',
      free_wait_until: '2026-11-02T09:15:00Z',
      expires_at: '2026-11-02T09:45:00Z',
      trip_started_at: '2026-11-02T09:05:00Z',
      ended_at: '2026-11-02T09:35:00Z',
      cancel_reason: null,
      inspection_submitted: true,
      after_inspection_submitted: true,
      cost: {
        lines: [{ kind: 'driving', minutes: 30, free_minutes: 0, per_minute: 40, amount: 1200 }],
        total: 1200,
      },
      warnings: [],
      charge_status: 'paid',
      receipt: null,
      adjustments: [],
      total: 1200,
      start: { lat: 53.9, lon: 27.56 },
      end: { lat: 53.91, lon: 27.57 },
      as_of: '2026-11-02T10:00:00Z',
    },
    client: { id: nextId(), first_name: 'Ivan', last_name: 'Petrov' },
    intervals: [
      { kind: 'driving', started_at: '2026-11-02T09:05:00Z', ended_at: '2026-11-02T09:35:00Z' },
    ],
    operations: [
      {
        id: holdId,
        kind: 'hold',
        amount: 3000,
        status: 'succeeded',
        parent_id: null,
        decline_reason: null,
        created_at: '2026-11-02T09:05:00Z',
      },
      {
        id: nextId(),
        kind: 'capture',
        amount: 1200,
        status: 'succeeded',
        parent_id: holdId,
        decline_reason: null,
        created_at: '2026-11-02T09:35:00Z',
      },
    ],
    inspections: [],
    adjustments: [],
    owed: 0,
    forced_finish: null,
    ...overrides,
  };
}

export function rentalItem(overrides: Partial<StaffRentalItem> = {}): StaffRentalItem {
  return {
    id: nextId(),
    status: 'paid',
    car: { id: nextId(), brand: 'Geely', model: 'Geometry C', plate: '1234 AB-7', photo_url: null },
    reserved_at: '2026-11-02T09:00:00Z',
    trip_started_at: '2026-11-02T09:05:00Z',
    ended_at: '2026-11-02T09:35:00Z',
    total: 1200,
    charge_status: 'paid',
    client: { id: nextId(), first_name: 'Ivan', last_name: 'Petrov' },
    ...overrides,
  };
}

/** The rentals API with `items` on one page and `card` open. */
export function fakeRentalsApi({
  items = [],
  card = staffRental(),
}: { items?: StaffRentalItem[]; card?: StaffRental } = {}) {
  return {
    list: vi.fn<RentalsApi['list']>(() => Promise.resolve({ items, next_cursor: null })),
    rental: vi.fn<RentalsApi['rental']>(() => Promise.resolve(card)),
    photoUrl: vi.fn<RentalsApi['photoUrl']>(() =>
      Promise.resolve({ url: 'http://storage.test/photo.jpg', expires_in: 300 }),
    ),
    forceFinish: vi.fn<RentalsApi['forceFinish']>(() =>
      Promise.resolve({ rental: card, locked: true }),
    ),
    adjust: vi.fn<RentalsApi['adjust']>(() => Promise.resolve(card)),
    retryRefunds: vi.fn<RentalsApi['retryRefunds']>(() => Promise.resolve(card)),
  } satisfies RentalsApi;
}
