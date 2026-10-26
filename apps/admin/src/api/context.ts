import { createContext, useContext } from 'react';

import type { AuditLogApi } from '../audit/api';
import type { StaffSignInApi } from '../auth/staffApi';
import type { KycReviewApi } from '../kyc/api';
import type { StaffListApi } from '../staff/api';
import type { TariffsApi } from '../tariffs/api';
import type { ZonesApi } from '../zones/api';

/** The API calls the pages make; tests put fakes here instead of the network. */
export interface AdminApis {
  auditLog: AuditLogApi;
  kycReview: KycReviewApi;
  staffList: StaffListApi;
  staffSignIn: StaffSignInApi;
  tariffs: TariffsApi;
  zones: ZonesApi;
}

export const ApiContext = createContext<AdminApis | null>(null);

export function useApis(): AdminApis {
  const apis = useContext(ApiContext);
  if (!apis) throw new Error('useApis is used outside of ApiContext');
  return apis;
}
