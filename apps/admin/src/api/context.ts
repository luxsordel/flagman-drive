import { createContext, useContext } from 'react';

import type { AuditLogApi } from '../audit/api';
import type { StaffSignInApi } from '../auth/staffApi';
import type { ClientsApi } from '../clients/api';
import type { FinesApi } from '../fines/api';
import type { FleetApi } from '../fleet/api';
import type { OpenFleetStream } from '../fleet/stream';
import type { KycReviewApi } from '../kyc/api';
import type { RentalsApi } from '../rentals/api';
import type { StaffListApi } from '../staff/api';
import type { SupportApi } from '../support/api';
import type { TariffsApi } from '../tariffs/api';
import type { TasksApi } from '../tasks/api';
import type { ZonesApi } from '../zones/api';

/** The API calls the pages make; tests put fakes here instead of the network. */
export interface AdminApis {
  auditLog: AuditLogApi;
  clients: ClientsApi;
  fines: FinesApi;
  fleet: FleetApi;
  /** Opens the live fleet; a page closes what it opened. */
  fleetStream: OpenFleetStream;
  kycReview: KycReviewApi;
  rentals: RentalsApi;
  staffList: StaffListApi;
  staffSignIn: StaffSignInApi;
  support: SupportApi;
  tariffs: TariffsApi;
  tasks: TasksApi;
  zones: ZonesApi;
}

export const ApiContext = createContext<AdminApis | null>(null);

export function useApis(): AdminApis {
  const apis = useContext(ApiContext);
  if (!apis) throw new Error('useApis is used outside of ApiContext');
  return apis;
}
