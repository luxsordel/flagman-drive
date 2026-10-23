import { getLocales } from 'expo-localization';
import i18next from 'i18next';
import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from 'react';

import { detectLanguage, type Language } from '../../i18n';
import type { AuthSession } from '../auth/session/authSession';
import { createMockProfileApi, type Profile, type ProfileApi } from './profileApi';
import { followProfileLanguage, ProfileStore, type ProfileState } from './profileStore';

export function createAppProfileStore(session: AuthSession): ProfileStore {
  // One mock per signed-in client: the next client on this device must start from a blank profile.
  let mock: ProfileApi | null = null;
  const current = (): ProfileApi =>
    (mock ??= createMockProfileApi({ initial: blankProfile(session) }));
  const store = new ProfileStore({
    getProfile: () => current().getProfile(),
    updateProfile: (update) => current().updateProfile(update),
  });
  const applyLanguage = (language: Language) => {
    if (i18next.language !== language) void i18next.changeLanguage(language);
  };
  session.subscribe(() => {
    if (session.getStatus() !== 'signedOut') return;
    mock = null;
    store.reset();
    // The sign-in screen and the next client must not inherit the previous client's choice.
    applyLanguage(detectLanguage(getLocales()));
  });
  followProfileLanguage(store, applyLanguage);
  return store;
}

function blankProfile(session: AuthSession): Profile {
  return {
    first_name: '',
    last_name: '',
    birth_date: null,
    email: null,
    // The device language, as at first launch: not the one the previous client chose.
    language: detectLanguage(getLocales()),
    // The pending sign-in lives in memory only: after an app restart the mock has no number,
    // and the screen says so instead of showing a made-up one.
    phone: session.getPendingSignIn()?.phone ?? '',
  };
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
