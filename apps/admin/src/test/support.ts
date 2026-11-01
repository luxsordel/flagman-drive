import { vi } from 'vitest';

import type { Conversation, Incident, StaffMessage, SupportApi } from '../support/api';

let counter = 0;

const nextId = (): string => {
  counter += 1;
  return `0199c000-1111-7000-8000-${String(counter).padStart(12, '0')}`;
};

export function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: nextId(),
    user_id: nextId(),
    rental_id: null,
    rental_status: null,
    car_id: null,
    car_plate: null,
    status: 'open',
    assigned_staff_id: null,
    assigned_staff_name: null,
    unread: 1,
    first_name: 'Ivan',
    last_name: 'Petrov',
    last_message_text: 'The car smells of smoke',
    created_at: '2026-11-02T09:00:00Z',
    last_message_at: '2026-11-02T09:05:00Z',
    ...overrides,
  };
}

export function staffMessage(overrides: Partial<StaffMessage> = {}): StaffMessage {
  counter += 1;
  return {
    id: nextId(),
    seq: counter,
    author: 'client',
    staff_name: null,
    staff_id: null,
    kind: 'text',
    text: 'The car smells of smoke',
    created_at: '2026-11-02T09:05:00Z',
    ...overrides,
  };
}

export function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: nextId(),
    type: 'damage',
    status: 'new',
    description: 'Scratch on the rear door',
    location: null,
    rental_id: null,
    car_id: null,
    photos: [],
    created_at: '2026-11-02T09:00:00Z',
    updated_at: '2026-11-02T09:00:00Z',
    user_id: nextId(),
    first_name: 'Ivan',
    last_name: 'Petrov',
    priority: 1,
    assigned_staff_id: null,
    assigned_staff_name: null,
    rental_status: null,
    car_plate: null,
    sent_to_service: false,
    events: [],
    ...overrides,
  };
}

export function fakeSupportApi({
  unread = { conversations: 0, incidents: 0 },
  queue = [],
  card = conversation(),
  messages = [],
  incidents = [],
  card2 = incident(),
}: {
  unread?: Awaited<ReturnType<SupportApi['unread']>>;
  queue?: Conversation[];
  card?: Conversation;
  messages?: StaffMessage[];
  incidents?: Incident[];
  card2?: Incident;
} = {}) {
  return {
    unread: vi.fn<SupportApi['unread']>(() => Promise.resolve(unread)),
    queue: vi.fn<SupportApi['queue']>(() => Promise.resolve(queue)),
    conversation: vi.fn<SupportApi['conversation']>(() => Promise.resolve(card)),
    messages: vi.fn<SupportApi['messages']>((id, after) =>
      Promise.resolve({
        conversation_id: id,
        status: card.status,
        items: messages.filter((message) => message.seq > after),
      }),
    ),
    take: vi.fn<SupportApi['take']>(() => Promise.resolve(card)),
    reply: vi.fn<SupportApi['reply']>((_id, text) =>
      Promise.resolve(staffMessage({ author: 'staff', text })),
    ),
    close: vi.fn<SupportApi['close']>(() => Promise.resolve(card)),
    messagePhotoUrl: vi.fn<SupportApi['messagePhotoUrl']>(() =>
      Promise.resolve({ url: 'http://storage.test/photo.jpg', expires_in: 300 }),
    ),
    incidents: vi.fn<SupportApi['incidents']>(() =>
      Promise.resolve({ items: incidents, next_cursor: null }),
    ),
    incident: vi.fn<SupportApi['incident']>(() => Promise.resolve(card2)),
    takeIncident: vi.fn<SupportApi['takeIncident']>(() =>
      Promise.resolve({ ...card2, status: 'in_progress' as const }),
    ),
    resolveIncident: vi.fn<SupportApi['resolveIncident']>(() =>
      Promise.resolve({ ...card2, status: 'resolved' as const }),
    ),
    rejectIncident: vi.fn<SupportApi['rejectIncident']>(() =>
      Promise.resolve({ ...card2, status: 'rejected' as const }),
    ),
    sendToService: vi.fn<SupportApi['sendToService']>(() =>
      Promise.resolve({ ...card2, sent_to_service: true }),
    ),
    incidentPhotoUrl: vi.fn<SupportApi['incidentPhotoUrl']>(() =>
      Promise.resolve({ url: 'http://storage.test/photo.jpg', expires_in: 300 }),
    ),
  };
}
