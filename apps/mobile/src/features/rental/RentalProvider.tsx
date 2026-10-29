import { createApiClient } from '@flagman/api-client';
import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from 'react';
import { AppState } from 'react-native';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import { locateOnce } from '../map/location';
import { createRentalApi } from './rentalApi';
import { RentalStore, type RentalState } from './rentalStore';

export function createAppRentalStore(session: AuthSession): RentalStore {
  const baseUrl = appApiUrl();
  const api = createApiClient({ baseUrl, getAccessToken: session.getAccessToken });
  const store = new RentalStore(
    createRentalApi({ api, baseUrl, getAccessToken: session.getAccessToken }),
    locateOnce,
  );
  session.subscribe(() => {
    if (session.getStatus() === 'signedOut') store.reset();
  });
  return store;
}

const RentalContext = createContext<RentalStore | null>(null);

export function RentalProvider({ store, children }: { store: RentalStore; children: ReactNode }) {
  return <RentalContext value={store}>{children}</RentalContext>;
}

export function useRentalStore(): RentalStore {
  const store = useContext(RentalContext);
  if (store === null) throw new Error('useRentalStore must be used inside RentalProvider');
  return store;
}

export function useRentalState(): RentalState {
  const store = useRentalStore();
  return useSyncExternalStore(store.subscribe, store.getState);
}

/**
 * Comes back to the active rental when a screen showing it opens and whenever the app returns to
 * the foreground: it may have expired or started meanwhile, or the app may have been restarted.
 */
export function useRestoredRental(): RentalState {
  const store = useRentalStore();
  useEffect(() => {
    void store.restore();
    const subscription = AppState.addEventListener('change', (appState) => {
      if (appState === 'active') void store.restore();
    });
    return () => {
      subscription.remove();
    };
  }, [store]);
  return useRentalState();
}
