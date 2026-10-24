import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';

export type StaffMember = components['schemas']['StaffMemberSummary'];

export interface StaffListApi {
  /**
   * Every staff member by email, disabled ones included; admin only.
   * Rejects with ApiError when the API refuses, with NetworkError when it is unreachable.
   */
  list(): Promise<StaffMember[]>;
}

export function createStaffListApi(client: AdminApiClient): StaffListApi {
  return {
    async list() {
      return unwrap(await client.GET('/admin/staff')).items;
    },
  };
}
