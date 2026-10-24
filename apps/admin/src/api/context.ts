import { createContext, useContext } from 'react';

import type { AuditLogApi } from '../audit/api';
import type { StaffSignInApi } from '../auth/staffApi';

/** The API calls the pages make; tests put fakes here instead of the network. */
export interface AdminApis {
  auditLog: AuditLogApi;
  staffSignIn: StaffSignInApi;
}

export const ApiContext = createContext<AdminApis | null>(null);

export function useApis(): AdminApis {
  const apis = useContext(ApiContext);
  if (!apis) throw new Error('useApis is used outside of ApiContext');
  return apis;
}
