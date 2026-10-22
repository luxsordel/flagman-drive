import { vi } from 'vitest';

import type { StaffAuthApi } from '../auth/session';
import type { Staff, StaffRole } from '../auth/staff';

export function testStaff(role: StaffRole = 'admin'): Staff {
  return { id: '0199a7c2-5d1e-7b3a-9f00-000000000001', email: `${role}@example.com`, role };
}

/** The staff auth API with a live session for `staff`, or none when it is null. */
export function fakeStaffAuthApi(staff: Staff | null) {
  return {
    refresh: vi.fn<StaffAuthApi['refresh']>(() =>
      Promise.resolve(staff ? { accessToken: 'access-1', expiresInSeconds: 900 } : 'rejected'),
    ),
    me: vi.fn<StaffAuthApi['me']>(() =>
      staff ? Promise.resolve(staff) : Promise.reject(new Error('no session')),
    ),
    logout: vi.fn<StaffAuthApi['logout']>(() => Promise.resolve()),
  } satisfies StaffAuthApi;
}
