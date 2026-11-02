import type { SupportApi, SupportMessage, SupportResult } from './supportApi';

/** Test doubles shared by the support tests. */

export const ok = <T>(value: T): SupportResult<T> => ({ ok: true, value });
export const offline = { ok: false, error: { kind: 'network' } } as const;

let seq = 0;

export function message(overrides: Partial<SupportMessage> = {}): SupportMessage {
  seq += 1;
  return {
    id: `message-${String(seq)}`,
    seq,
    author: 'staff',
    staff_name: 'Anna',
    kind: 'text',
    text: 'How can we help?',
    created_at: '2026-11-02T09:00:00Z',
    ...overrides,
  };
}

export function page(items: SupportMessage[] = [], conversationId: string | null = 'c-1') {
  return ok({
    conversation_id: conversationId,
    status: conversationId === null ? null : ('open' as const),
    items,
  });
}

export function fakeSupportApi() {
  return {
    messages: jest.fn<ReturnType<SupportApi['messages']>, [number]>(() =>
      Promise.resolve(page([])),
    ),
    sendText: jest.fn<ReturnType<SupportApi['sendText']>, [string, string | null, string]>((text) =>
      Promise.resolve(ok(message({ author: 'client', staff_name: null, text }))),
    ),
    sendPhoto: jest.fn<ReturnType<SupportApi['sendPhoto']>, [string, string | null, string]>(() =>
      Promise.resolve(
        ok(message({ author: 'client', staff_name: null, kind: 'photo', text: null })),
      ),
    ),
    photoUrl: jest.fn<ReturnType<SupportApi['photoUrl']>, [string]>(() =>
      Promise.resolve(ok('https://storage.test/photo.jpg')),
    ),
    reportIncident: jest.fn<
      ReturnType<SupportApi['reportIncident']>,
      Parameters<SupportApi['reportIncident']>
    >(() =>
      Promise.resolve(
        ok({
          id: 'incident-1',
          type: 'damage',
          status: 'new',
          description: 'Scratch',
          location: null,
          rental_id: null,
          car_id: null,
          photos: [],
          created_at: '2026-11-02T09:00:00Z',
          updated_at: '2026-11-02T09:00:00Z',
        }),
      ),
    ),
    addIncidentPhoto: jest.fn<ReturnType<SupportApi['addIncidentPhoto']>, [string, string]>(() =>
      Promise.resolve(ok(null)),
    ),
  };
}
