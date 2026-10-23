import { vi } from 'vitest';

import type { AuditEvent, AuditEventPage, AuditLogApi } from '../audit/api';

let counter = 0;

export function auditEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  counter += 1;
  return {
    id: `0199a7c2-0000-7000-8000-${String(counter).padStart(12, '0')}`,
    occurred_at: '2026-10-23T12:04:05Z',
    actor_type: 'staff',
    actor_id: '0199a7c2-5d1e-7b3a-9f00-000000000001',
    action: 'staff.signed_in',
    target: null,
    ip_hash: null,
    request_id: null,
    details: {},
    ...overrides,
  };
}

/** The audit log API answering with `pages` one by one, then with an empty last page. */
export function fakeAuditLogApi(...pages: AuditEventPage[]) {
  const list = vi.fn<AuditLogApi['list']>();
  for (const page of pages) list.mockResolvedValueOnce(page);
  list.mockResolvedValue({ items: [], next_cursor: null });
  return { list } satisfies AuditLogApi;
}
