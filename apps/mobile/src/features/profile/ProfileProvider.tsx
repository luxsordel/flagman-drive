import { createApiClient } from '@flagman/api-client';
import { getLocales } from 'expo-localization';
import i18next from 'i18next';
import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from 'react';

import { detectLanguage, type Language } from '../../i18n';
import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import { createProfileApi } from './profileApi';
import { followProfileLanguage, ProfileStore, type ProfileState } from './profileStore';

export function createAppProfileStore(
  session: AuthSession,
  api = createApiClient({ baseUrl: appApiUrl(), getAccessToken: session.getAccessToken }),
): ProfileStore {
  const store = new ProfileStore(createProfileApi(api));
  const applyLanguage = (language: Language) => {
    if (i18next.language !== language) void i18next.changeLanguage(language);
  };
  followProfileLanguage(store, applyLanguage);
  let status = session.getStatus();
  // Loaded at sign-in and after a restored session, not when the profile screen opens:
  // the app must speak the client's language from the first screen.
  if (status === 'signedIn') void store.load();
  session.subscribe(() => {
    const previous = status;
    status = session.getStatus();
    if (status === previous) return;
    if (status === 'signedIn') void store.load();
    if (status !== 'signedOut') return;
    store.reset();
    // The sign-in screen and the next client must not inherit the previous client's choice.
    applyLanguage(detectLanguage(getLocales()));
  });
  return store;
}

const ProfileContext = createContext<ProfileStore | null>(null);

export function ProfileProvider({ store, children }: { store: ProfileStore; children: ReactNode }) {
  return <ProfileContext value={store}>{children}</ProfileContext>;
}

export function useProfileStore(): ProfileStore {
  const store = useContext(ProfileContext);
  if (store === null) throw new Error('useProfileStore must be used inside ProfileProvider');
  return store;
}

/** The profile, reloaded whenever a screen showing it opens. */
export function useProfile(): ProfileState {
  const store = useProfileStore();
  useEffect(() => {
    void store.load();
  }, [store]);
  return useSyncExternalStore(store.subscribe, store.getState);
}
