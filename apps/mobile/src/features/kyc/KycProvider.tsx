import { createApiClient } from '@flagman/api-client';
import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from 'react';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import { createKycApi } from './kycApi';
import { KycStore, type KycState } from './kycStore';

export function createAppKycStore(session: AuthSession): KycStore {
  const baseUrl = appApiUrl();
  const api = createApiClient({ baseUrl, getAccessToken: session.getAccessToken });
  const store = new KycStore(
    createKycApi({ api, baseUrl, getAccessToken: session.getAccessToken }),
  );
  session.subscribe(() => {
    if (session.getStatus() === 'signedOut') store.reset();
  });
  return store;
}

const KycContext = createContext<KycStore | null>(null);

export function KycProvider({ store, children }: { store: KycStore; children: ReactNode }) {
  return <KycContext value={store}>{children}</KycContext>;
}

export function useKycStore(): KycStore {
  const store = useContext(KycContext);
  if (store === null) throw new Error('useKycStore must be used inside KycProvider');
  return store;
}

/** The application as last loaded; call `useKycStore().load()` to refresh it. */
export function useKycState(): KycState {
  const store = useKycStore();
  return useSyncExternalStore(store.subscribe, store.getState);
}

/** The application, reloaded whenever a screen showing it opens: an operator may have decided. */
export function useKycApplication(): KycState {
  const store = useKycStore();
  useEffect(() => {
    void store.load();
  }, [store]);
  return useKycState();
}
