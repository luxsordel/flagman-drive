import { vi } from 'vitest';

import type { StaffAuthApi } from '../auth/session';
import type { StaffSignInApi } from '../auth/staffApi';
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

/** Sign-in steps that accept any password and code; `enrolled: false` asks for the 2FA setup first. */
export function fakeStaffSignInApi({ enrolled = true }: { enrolled?: boolean } = {}) {
  return {
    checkPassword: vi.fn<StaffSignInApi['checkPassword']>(() =>
      Promise.resolve({
        challenge_token: 'challenge-1',
        expires_in: 300,
        next_step: enrolled ? 'totp' : 'totp_enrollment',
      }),
    ),
    startTotpEnrollment: vi.fn<StaffSignInApi['startTotpEnrollment']>(() =>
      Promise.resolve({
        otpauth_uri: 'otpauth://totp/FLAGMAN:operator@example.com?secret=JBSWY3DPEHPK3PXP',
        secret: 'JBSWY3DPEHPK3PXP',
      }),
    ),
    verifyTotp: vi.fn<StaffSignInApi['verifyTotp']>(() =>
      Promise.resolve({ accessToken: 'access-1', expiresInSeconds: 900 }),
    ),
  } satisfies StaffSignInApi;
}
