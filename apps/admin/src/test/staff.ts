import { vi } from 'vitest';

import type { StaffListApi, StaffMember } from '../staff/api';

/** The staff list API answering with `members`. */
export function fakeStaffListApi(...members: StaffMember[]) {
  const list = vi.fn<StaffListApi['list']>(() => Promise.resolve(members));
  return { list } satisfies StaffListApi;
}
