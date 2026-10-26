import { createApiClient } from '@flagman/api-client';
import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import { createMapApi } from './mapApi';
import { MapStore, type MapState } from './mapStore';

export function createAppMapStore(session: AuthSession): MapStore {
  const api = createApiClient({ baseUrl: appApiUrl(), getAccessToken: session.getAccessToken });
  const store = new MapStore(createMapApi(api));
  session.subscribe(() => {
    if (session.getStatus() === 'signedOut') store.reset();
  });
  return store;
}

const MapContext = createContext<MapStore | null>(null);

export function MapProvider({ store, children }: { store: MapStore; children: ReactNode }) {
  return <MapContext value={store}>{children}</MapContext>;
}

export function useMapStore(): MapStore {
  const store = useContext(MapContext);
  if (store === null) throw new Error('useMapStore must be used inside MapProvider');
  return store;
}

export function useMapState(): MapState {
  const store = useMapStore();
  return useSyncExternalStore(store.subscribe, store.getState);
}
