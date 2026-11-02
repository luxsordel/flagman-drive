import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';

export type StaffClient = components['schemas']['StaffClientOut'];
export type ClientSummary = components['schemas']['ClientSummaryOut'];
export type ClientPage = components['schemas']['ClientPageOut'];

/** BLOCK_REASON_MAX_LENGTH of the API (identity/domain.py); the search needs two characters. */
export const blockReasonMaxLength = 500;
export const clientSearchMinLength = 2;

export interface ClientFilter {
  search?: string;
  blocked?: boolean;
}

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface ClientsApi {
  list(filter: ClientFilter, cursor?: string): Promise<ClientPage>;
  client(id: string): Promise<StaffClient>;
  block(id: string, reason: string): Promise<StaffClient>;
  unblock(id: string, reason: string): Promise<StaffClient>;
}

export function createClientsApi(client: AdminApiClient): ClientsApi {
  const clientPath = (id: string) => ({ params: { path: { user_id: id } } });
  return {
    async list(filter, cursor) {
      return unwrap(
        await client.GET('/admin/clients', {
          params: {
            query: {
              ...(filter.search ? { search: filter.search } : {}),
              ...(filter.blocked === undefined ? {} : { blocked: filter.blocked }),
              ...(cursor ? { cursor } : {}),
            },
          },
        }),
      );
    },
    async client(id) {
      return unwrap(await client.GET('/admin/clients/{user_id}', clientPath(id)));
    },
    async block(id, reason) {
      return unwrap(
        await client.POST('/admin/clients/{user_id}/block', {
          ...clientPath(id),
          body: { reason },
        }),
      );
    },
    async unblock(id, reason) {
      return unwrap(
        await client.POST('/admin/clients/{user_id}/unblock', {
          ...clientPath(id),
          body: { reason },
        }),
      );
    },
  };
}
