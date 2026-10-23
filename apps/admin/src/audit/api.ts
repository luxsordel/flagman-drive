import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';
import { minskDayStart, nextDay } from './minskTime';

export type AuditEvent = components['schemas']['AuditEventOut'];
export type AuditEventPage = components['schemas']['AuditEventPage'];
export type AuditAction = components['schemas']['AuditAction'];

export interface AuditLogFilter {
  actorId?: string;
  action?: AuditAction;
  /** First and last Minsk calendar days (`YYYY-MM-DD`), both included. */
  from?: string;
  to?: string;
}

export const auditPageSize = 50;

export interface AuditLogApi {
  /**
   * Newest events first; `cursor` is `next_cursor` of the previous page.
   * Rejects with ApiError when the API refuses, with the fetch error when it is unreachable.
   */
  list(filter: AuditLogFilter, cursor?: string): Promise<AuditEventPage>;
}

export function createAuditLogApi(client: AdminApiClient): AuditLogApi {
  return {
    async list(filter, cursor) {
      const result = await client.GET('/admin/audit-events', {
        params: {
          query: {
            limit: auditPageSize,
            ...(filter.actorId ? { actor_id: filter.actorId } : {}),
            ...(filter.action ? { action: filter.action } : {}),
            ...(filter.from ? { occurred_from: minskDayStart(filter.from) } : {}),
            // The API's upper bound is exclusive: the next day's start keeps the whole last day.
            ...(filter.to ? { occurred_to: minskDayStart(nextDay(filter.to)) } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
      });
      return unwrap(result);
    },
  };
}
