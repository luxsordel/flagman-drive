import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import i18next from 'i18next';
import { AccessibilityInfo } from 'react-native';

import { initI18n } from '../../i18n';
import { AuthProvider } from '../auth/session/AuthProvider';
import { testSession } from '../auth/session/testing';
import { KycProvider } from '../kyc/KycProvider';
import { KycStore } from '../kyc/kycStore';
import { fakeKycApi, kycApplication, ok as kycOk } from '../kyc/testing';
import type { KycStatus } from '../kyc/kycApi';
import type { ProfileResult } from './profileApi';
import { ProfileProvider } from './ProfileProvider';
import { ProfileScreen } from './ProfileScreen';
import { followProfileLanguage, ProfileStore } from './profileStore';
import { fakeProfileApi, ok, profile } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

async function renderProfile(
  firstLoad: ProfileResult = ok(profile({ email: 'ivan@example.com' })),
  kycStatus: KycStatus = 'not_submitted',
) {
  const auth = testSession({ refreshToken: 'refresh-0' });
  const kyc = fakeKycApi();
  kyc.api.getApplication.mockResolvedValue(kycOk(kycApplication([], { status: kycStatus })));
  const api = fakeProfileApi();
  api.getProfile.mockResolvedValue(firstLoad);
  const store = new ProfileStore(api);
  await render(
    <AuthProvider session={auth.session}>
      <KycProvider store={new KycStore(kyc.api)}>
        <ProfileProvider store={store}>
          <ProfileScreen />
        </ProfileProvider>
      </KycProvider>
    </AuthProvider>,
  );
  // The form appears once the profile is loaded.
  if (firstLoad.ok) await screen.findByLabelText('Имя');
  return { auth, api, store, kycApi: kyc.api };
}

const field = (label: string) => screen.getByLabelText(label);
const saveButton = () => screen.getByRole('button', { name: 'Сохранить' });

describe('ProfileScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(async () => {
    await i18next.changeLanguage('ru');
  });

  it('shows the profile, the phone read-only and the document status', async () => {
    await renderProfile();

    expect(field('Имя')).toHaveDisplayValue('Ivan');
    expect(field('Фамилия')).toHaveDisplayValue('Petrov');
    expect(field('Дата рождения')).toHaveDisplayValue('15.03.1995');
    expect(field('Email')).toHaveDisplayValue('ivan@example.com');
    expect(screen.getByText('+375 29 123-45-67')).toBeOnTheScreen();
    expect(screen.queryByDisplayValue('+375 29 123-45-67')).toBeNull();
    expect(screen.getByRole('radio', { name: 'Русский' })).toBeChecked();
    expect(await screen.findByText('Не отправлены')).toBeOnTheScreen();
  });

  it.each(['pending', 'approved'] as const)(
    'keeps the name and birth date read-only while the documents are %s',
    async (status) => {
      const { api } = await renderProfile(
        ok(profile({ first_name: null, last_name: null, birth_date: null })),
        status,
      );
      api.updateProfile.mockResolvedValue(ok(profile({ email: 'anna@example.com' })));

      expect(
        await screen.findByText(/^Фамилию, имя и дату рождения нельзя изменить/),
      ).toBeOnTheScreen();
      expect(field('Имя')).toHaveProp('editable', false);
      expect(field('Фамилия')).toHaveProp('editable', false);
      expect(field('Дата рождения')).toHaveProp('editable', false);
      expect(field('Email')).toHaveProp('editable', true);

      await fireEvent.changeText(field('Email'), 'anna@example.com');
      await fireEvent.press(saveButton());

      expect(await screen.findByText('Изменения сохранены')).toBeOnTheScreen();
      expect(api.updateProfile).toHaveBeenCalledWith({ email: 'anna@example.com' });
    },
  );

  it('explains a name change refused because the documents went for review', async () => {
    const { api, kycApi } = await renderProfile();
    api.updateProfile.mockResolvedValue({ ok: false, error: { kind: 'locked' } });
    kycApi.getApplication.mockResolvedValue(kycOk(kycApplication([], { status: 'pending' })));

    await fireEvent.changeText(field('Имя'), 'Anna');
    await fireEvent.press(saveButton());

    expect(
      await screen.findByText(/^Документы уже на проверке, поэтому фамилию/),
    ).toBeOnTheScreen();
    expect(screen.queryByText('Что-то пошло не так. Попробуйте ещё раз')).toBeNull();
    // The status reloaded, so the fields it locks are read-only now.
    await waitFor(() => {
      expect(field('Имя')).toHaveProp('editable', false);
    });
  });

  it('shows blanks for names the client has not filled in yet', async () => {
    await renderProfile(ok(profile({ first_name: null, last_name: null, birth_date: null })));

    expect(field('Имя')).toHaveDisplayValue('');
    expect(field('Фамилия')).toHaveDisplayValue('');
    expect(field('Дата рождения')).toHaveDisplayValue('');
  });

  it('saves the edited fields and shows what was kept', async () => {
    const { api } = await renderProfile();
    let answer: (result: ProfileResult) => void = () => undefined;
    api.updateProfile.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );

    await fireEvent.changeText(field('Имя'), ' Anna ');
    await fireEvent.changeText(field('Email'), 'anna@example.com');
    await fireEvent.press(saveButton());

    expect(screen.getByRole('button', { name: 'Сохраняем…' })).toBeDisabled();
    expect(api.updateProfile).toHaveBeenCalledWith({
      first_name: 'Anna',
      last_name: 'Petrov',
      birth_date: '1995-03-15',
      email: 'anna@example.com',
    });

    answer(ok(profile({ first_name: 'Anna', email: 'anna@example.com' })));

    expect(await screen.findByText('Изменения сохранены')).toBeOnTheScreen();
    expect(field('Имя')).toHaveDisplayValue('Anna');
    expect(saveButton()).toBeEnabled();
  });

  it('hides the confirmation once the client edits again', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockResolvedValue(ok(profile()));

    await fireEvent.press(saveButton());
    expect(await screen.findByText('Изменения сохранены')).toBeOnTheScreen();
    await fireEvent.changeText(field('Фамилия'), 'Sidorov');

    expect(screen.queryByText('Изменения сохранены')).toBeNull();
  });

  it('puts the dots into the date of birth', async () => {
    await renderProfile();

    await fireEvent.changeText(field('Дата рождения'), '01022000');

    expect(field('Дата рождения')).toHaveDisplayValue('01.02.2000');
  });

  it('shows field errors and sends nothing', async () => {
    const { api } = await renderProfile();

    await fireEvent.changeText(field('Имя'), '');
    await fireEvent.changeText(field('Фамилия'), 'a'.repeat(101));
    await fireEvent.changeText(field('Дата рождения'), '31021995');
    await fireEvent.changeText(field('Email'), 'anna@');
    await fireEvent.press(saveButton());

    expect(screen.getByText('Заполните это поле')).toBeOnTheScreen();
    expect(screen.getByText('Слишком длинно. Максимум символов: 100')).toBeOnTheScreen();
    expect(screen.getByText('Такой даты нет. Проверьте день, месяц и год')).toBeOnTheScreen();
    expect(
      screen.getByText('Проверьте адрес: он должен быть вида name@example.com'),
    ).toBeOnTheScreen();
    expect(api.updateProfile).not.toHaveBeenCalled();

    await fireEvent.changeText(field('Имя'), 'Anna');
    expect(screen.queryByText('Заполните это поле')).toBeNull();
  });

  it('explains a failed save and clears it on the next attempt', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await fireEvent.press(saveButton());
    expect(await screen.findByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();

    await fireEvent.changeText(field('Имя'), '');
    await fireEvent.press(saveButton());

    expect(screen.queryByText('Нет связи с сервером. Проверьте интернет')).toBeNull();
    expect(screen.getByText('Заполните это поле')).toBeOnTheScreen();
  });

  it('shows a date the API refused at the date field', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockResolvedValue({ ok: false, error: { kind: 'birthDateInvalid' } });

    await fireEvent.press(saveButton());

    expect(await screen.findByText('Сервер не принял эту дату. Проверьте её')).toBeOnTheScreen();
    expect(screen.queryByText('Сервер не принял дату рождения. Проверьте её')).toBeNull();
  });

  it('switches the app language through the profile', async () => {
    const { api, store } = await renderProfile();
    followProfileLanguage(store, (language) => void i18next.changeLanguage(language));
    api.updateProfile.mockResolvedValue(ok(profile({ language: 'en' })));

    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));

    expect(api.updateProfile).toHaveBeenCalledWith({ language: 'en' });
    expect(await screen.findByText('Profile')).toBeOnTheScreen();
    expect(screen.getByRole('radio', { name: 'English' })).toBeChecked();
  });

  it('keeps the language and explains when the choice is not saved', async () => {
    const { api, store } = await renderProfile();
    followProfileLanguage(store, (language) => void i18next.changeLanguage(language));
    api.updateProfile.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));

    expect(await screen.findByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();
    expect(screen.getByRole('radio', { name: 'Русский' })).toBeChecked();
    expect(i18next.language).toBe('ru');
  });

  it('keeps what the client typed when the profile changes meanwhile', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockResolvedValue(ok(profile({ first_name: 'Ivan', language: 'en' })));

    await fireEvent.changeText(field('Имя'), 'Anna');
    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));

    await waitFor(() => {
      expect(screen.getByRole('radio', { name: 'English' })).toBeChecked();
    });
    expect(field('Имя')).toHaveDisplayValue('Anna');
  });

  it('takes fresh data into a form the client has not touched', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockResolvedValue(ok(profile({ first_name: 'Anna', language: 'en' })));

    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));

    expect(await screen.findByDisplayValue('Anna')).toBeOnTheScreen();
  });

  it('locks the form and the language while a save is in flight', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockReturnValue(new Promise(() => undefined));

    await fireEvent.press(saveButton());

    expect(field('Имя')).toHaveProp('editable', false);
    expect(screen.getByRole('radio', { name: 'English' })).toBeDisabled();
    await fireEvent(field('Email'), 'submitEditing');
    expect(api.updateProfile).toHaveBeenCalledTimes(1);
  });

  it('saves from the keyboard on the last field', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockResolvedValue(ok(profile()));

    await fireEvent(field('Email'), 'submitEditing');

    expect(await screen.findByText('Изменения сохранены')).toBeOnTheScreen();
    expect(api.updateProfile).toHaveBeenCalledTimes(1);
  });

  it('lets the client leave while a save is in flight', async () => {
    const { api, store } = await renderProfile();
    let answer: (result: ProfileResult) => void = () => undefined;
    api.updateProfile.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    const announce = jest.spyOn(AccessibilityInfo, 'announceForAccessibility');

    await fireEvent.press(saveButton());
    await screen.unmount();
    answer(ok(profile()));

    await waitFor(() => {
      expect(store.getState().saving).toBe(false);
    });
    // The confirmation belongs to a screen that is gone: nothing may be announced.
    expect(announce).not.toHaveBeenCalled();
    announce.mockRestore();
  });

  it('sends one language choice at a time', async () => {
    const { api } = await renderProfile();
    api.updateProfile.mockReturnValue(new Promise(() => undefined));

    await fireEvent.press(screen.getByRole('radio', { name: 'English' }));
    await fireEvent.press(screen.getByRole('radio', { name: 'Русский' }));

    expect(api.updateProfile).toHaveBeenCalledTimes(1);
    expect(api.updateProfile).toHaveBeenCalledWith({ language: 'en' });
  });

  it('offers a retry when the profile does not load', async () => {
    const { api } = await renderProfile({ ok: false, error: { kind: 'network' } });
    api.getProfile.mockResolvedValue(ok(profile()));

    await fireEvent.press(await screen.findByRole('button', { name: 'Повторить' }));

    expect(await screen.findByDisplayValue('Ivan')).toBeOnTheScreen();
  });

  it('goes back home', async () => {
    await renderProfile();

    await fireEvent.press(screen.getByRole('button', { name: 'На главную' }));

    expect(mockRouter.dismissTo).toHaveBeenCalledWith('/');
  });

  it('signs out', async () => {
    const { auth } = await renderProfile();
    auth.api.on('/auth/token/refresh', new Response('{}', { status: 500 }));
    auth.api.on('/auth/logout', new Response(null, { status: 204 }));
    await auth.session.restore();

    await fireEvent.press(screen.getByRole('button', { name: 'Выйти' }));

    await waitFor(() => {
      expect(auth.session.getStatus()).toBe('signedOut');
    });
    expect(auth.api.calls.at(-1)).toEqual({
      path: '/auth/logout',
      body: { refresh_token: 'refresh-0' },
    });
  });
});
