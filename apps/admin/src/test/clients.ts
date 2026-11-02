import { vi } from 'vitest';

import type { ClientsApi, ClientSummary, StaffClient } from '../clients/api';

let counter = 0;

const nextId = (): string => {
  counter += 1;
  return `0199c000-3333-7000-8000-${String(counter).padStart(12, '0')}`;
};

export function staffClient(overrides: Partial<StaffClient> = {}): StaffClient {
  return {
    id: nextId(),
    phone: '+375291234567',
    first_name: 'Ivan',
    last_name: 'Petrov',
    birth_date: '1990-05-17',
    email: null,
    language: 'ru',
    created_at: '2026-10-01T09:00:00Z',
    block: null,
    block_history: [],
    kyc_status: 'approved',
    debt: 0,
    ...overrides,
  };
}

export function clientSummary(overrides: Partial<ClientSummary> = {}): ClientSummary {
  return {
    id: nextId(),
    phone: '+375291234567',
    first_name: 'Ivan',
    last_name: 'Petrov',
    created_at: '2026-10-01T09:00:00Z',
    blocked: false,
    ...overrides,
  };
}

/** The clients API with `items` on one page and `card` open; a block blocks the card. */
export function fakeClientsApi({
  items = [],
  card = staffClient(),
}: { items?: ClientSummary[]; card?: StaffClient } = {}) {
  return {
    list: vi.fn<ClientsApi['list']>(() => Promise.resolve({ items, next_cursor: null })),
    client: vi.fn<ClientsApi['client']>(() => Promise.resolve(card)),
    block: vi.fn<ClientsApi['block']>((_id, reason) =>
      Promise.resolve({
        ...card,
        block: { blocked_at: '2026-11-02T10:00:00Z', reason, staff_id: null, staff_name: 'Anna' },
      }),
    ),
    unblock: vi.fn<ClientsApi['unblock']>(() => Promise.resolve({ ...card, block: null })),
  } satisfies ClientsApi;
}
