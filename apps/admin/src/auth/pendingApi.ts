import type { StaffAuthApi } from './session';

/**
 * Stands in until the staff endpoints land (#32) and the client is regenerated: there is no way
 * to sign in yet, so every refresh is rejected and the panel always opens on the sign-in page.
 */
export const pendingStaffAuthApi: StaffAuthApi = {
  refresh: () => Promise.resolve('rejected'),
  me: () => Promise.reject(new Error('Staff sign-in is not available yet')),
  logout: () => Promise.resolve(),
};
