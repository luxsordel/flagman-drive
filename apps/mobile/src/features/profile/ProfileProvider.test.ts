import { getLocales } from 'expo-localization';
import i18next from 'i18next';

import { detectLanguage, initI18n, type Language } from '../../i18n';
import { json, testSession, tokens } from '../auth/session/testing';
import { createAppProfileStore } from './ProfileProvider';
import { profile } from './testing';

initI18n('ru');

const device = detectLanguage(getLocales());
const other: Language = device === 'en' ? 'ru' : 'en';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(options: Parameters<typeof testSession>[0] = {}) {
  const auth = testSession(options);
  return { ...auth, profiles: createAppProfileStore(auth.session, auth.api.client) };
}

async function signIn({ api, session }: ReturnType<typeof testSession>) {
  api.on('/auth/otp/request', json({ expires_in: 300, resend_after: 60 }, 202));
  api.on('/auth/otp/verify', json(tokens(1)));
  await session.requestCode('+375291234567');
  await session.verifyCode('123456');
}

async function signOut({ api, session }: ReturnType<typeof testSession>) {
  api.on('/auth/logout', new Response(null, { status: 204 }));
  await session.signOut();
}

describe('createAppProfileStore', () => {
  afterEach(async () => {
    await i18next.changeLanguage('ru');
  });

  it('loads the profile at sign-in and speaks its language before any screen opens it', async () => {
    const auth = setup();
    auth.api.on('/me', json(profile({ language: other })));

    await signIn(auth);
    await flush();

    expect(auth.profiles.getState().profile).toEqual(profile({ language: other }));
    expect(i18next.language).toBe(other);
  });

  it('loads the profile when a stored session is restored at launch', async () => {
    const auth = setup({ refreshToken: 'refresh-0' });
    auth.api.on('/auth/token/refresh', json(tokens(1)));
    auth.api.on('/me', json(profile({ language: other })));

    await auth.session.restore();
    await flush();

    expect(auth.profiles.getState().profile?.phone).toBe('+375291234567');
    expect(i18next.language).toBe(other);
  });

  it('loads at once when the client is already signed in', async () => {
    const auth = testSession();
    await signIn(auth);
    auth.api.on('/me', json(profile()));

    const store = createAppProfileStore(auth.session, auth.api.client);
    await flush();

    expect(store.getState().profile).toEqual(profile());
  });

  it('does not reload the profile when the session changes without signing in again', async () => {
    const auth = setup();
    auth.api.on('/me', json(profile()));
    await signIn(auth);
    await flush();

    await auth.session.requestCode('+375291234567').catch(() => undefined);
    await flush();

    expect(auth.api.calls.filter((call) => call.path === '/me')).toHaveLength(1);
  });

  it('leaves no endless loading when the API cannot be reached', async () => {
    const auth = setup();
    auth.api.on('/me', new TypeError('Network request failed'));

    await signIn(auth);
    await flush();

    expect(auth.profiles.getState()).toMatchObject({
      profile: null,
      loading: false,
      loadError: { kind: 'network' },
    });
  });

  it('forgets the profile and gives the sign-in screen the device language back', async () => {
    const auth = setup();
    auth.api.on('/me', json(profile({ language: other })));
    await signIn(auth);
    await flush();
    expect(i18next.language).toBe(other);

    await signOut(auth);

    expect(auth.profiles.getState().profile).toBeNull();
    expect(i18next.language).toBe(device);
  });

  it('switches the app language to the one the client saves', async () => {
    const auth = setup();
    auth.api.on('/me', json(profile({ language: device })));
    await signIn(auth);
    await flush();

    auth.api.on('/me', json(profile({ language: other })));
    await auth.profiles.save({ language: other });

    expect(i18next.language).toBe(other);
  });
});
