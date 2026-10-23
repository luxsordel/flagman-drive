import { getLocales } from 'expo-localization';
import i18next from 'i18next';

import { detectLanguage, initI18n } from '../../i18n';
import { json, testSession, tokens } from '../auth/session/testing';
import { createAppProfileStore } from './ProfileProvider';

initI18n('ru');

async function signIn(setup: ReturnType<typeof testSession>, phone: string) {
  setup.api.on('/auth/otp/request', json({ expires_in: 300, resend_after: 60 }, 202));
  setup.api.on('/auth/otp/verify', json(tokens(1)));
  await setup.session.requestCode(phone);
  await setup.session.verifyCode('123456');
}

/** The mock answers after a simulated round trip; fake timers skip the wait. */
async function settle<T>(request: Promise<T>): Promise<T> {
  await jest.runAllTimersAsync();
  return request;
}

describe('createAppProfileStore', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(async () => {
    jest.useRealTimers();
    await i18next.changeLanguage('ru');
  });

  it('starts a client with a blank profile, their phone and the device language', async () => {
    const setup = testSession();
    const store = createAppProfileStore(setup.session);
    await signIn(setup, '+375291234567');

    await settle(store.load());

    expect(store.getState().profile).toEqual({
      first_name: '',
      last_name: '',
      birth_date: null,
      email: null,
      language: detectLanguage(getLocales()),
      phone: '+375291234567',
    });
  });

  it('leaves the phone empty when the number is no longer known', async () => {
    const setup = testSession();
    const store = createAppProfileStore(setup.session);

    await settle(store.load());

    expect(store.getState().profile?.phone).toBe('');
  });

  it('never shows one client the profile of the previous one', async () => {
    const setup = testSession();
    const store = createAppProfileStore(setup.session);
    await signIn(setup, '+375291234567');
    await settle(store.save({ first_name: 'Ivan', email: 'ivan@example.com', language: 'en' }));

    setup.api.on('/auth/logout', new Response(null, { status: 204 }));
    await setup.session.signOut();
    expect(store.getState().profile).toBeNull();

    await signIn(setup, '+375447654321');
    await settle(store.load());
    expect(store.getState().profile).toEqual({
      first_name: '',
      last_name: '',
      birth_date: null,
      email: null,
      language: detectLanguage(getLocales()),
      phone: '+375447654321',
    });
  });

  it('switches the app language to the one kept in the profile', async () => {
    const setup = testSession();
    const store = createAppProfileStore(setup.session);

    await settle(store.save({ language: 'en' }));

    expect(i18next.language).toBe('en');
  });
});
