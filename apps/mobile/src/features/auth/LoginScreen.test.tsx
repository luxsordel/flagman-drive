import { act, fireEvent, render, screen } from '@testing-library/react-native';
import i18next from 'i18next';

import { initI18n } from '../../i18n';
import { LoginScreen } from './LoginScreen';
import { AuthProvider } from './session/AuthProvider';
import { json, problem, testSession } from './session/testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), back: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

async function renderLogin() {
  const setup = testSession();
  await render(
    <AuthProvider session={setup.session}>
      <LoginScreen />
    </AuthProvider>,
  );
  return setup;
}

async function submit(phone: string) {
  await fireEvent.changeText(screen.getByTestId('login-phone'), phone);
  await fireEvent.press(screen.getByRole('button', { name: 'Получить код' }));
}

describe('LoginScreen', () => {
  afterEach(async () => {
    jest.clearAllMocks();
    // The screen is still mounted here; the language switch re-renders it.
    await act(() => i18next.changeLanguage('ru'));
  });

  it('keeps the button disabled until the number is a Belarusian mobile', async () => {
    await renderLogin();
    const button = screen.getByRole('button', { name: 'Получить код' });
    expect(button).toBeDisabled();

    await fireEvent.changeText(screen.getByTestId('login-phone'), '17 123 45 67');
    expect(button).toBeDisabled();

    await fireEvent.changeText(screen.getByTestId('login-phone'), '29 123 45 67');
    expect(button).toBeEnabled();
  });

  it('requests a code for the normalised number and opens the code step', async () => {
    const { api } = await renderLogin();
    api.on('/auth/otp/request', json({ expires_in: 300, resend_after: 60 }, 202));

    await submit('29 123-45-67');

    expect(await screen.findByRole('button', { name: 'Получить код' })).toBeEnabled();
    expect(api.calls).toEqual([{ path: '/auth/otp/request', body: { phone: '+375291234567' } }]);
    expect(mockRouter.push).toHaveBeenCalledWith('/code');
  });

  it.each([
    [
      problem('rate-limited', 429, { 'Retry-After': '42' }),
      'Слишком много попыток. Повторите через 0:42',
    ],
    [problem('sign-in-not-allowed', 403), 'Пилот пока открыт только для тестовых номеров'],
    [problem('sign-in-unavailable', 503), 'Вход временно недоступен. Попробуйте позже'],
    [new TypeError('Network request failed'), 'Нет связи с сервером. Проверьте интернет'],
  ])('explains a failure: %#', async (reply, message) => {
    const { api } = await renderLogin();
    api.on('/auth/otp/request', reply);

    await submit('291234567');

    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it('hides the error once the number is edited', async () => {
    const { api } = await renderLogin();
    api.on('/auth/otp/request', problem('sign-in-unavailable', 503));
    await submit('291234567');
    await screen.findByRole('alert');

    await fireEvent.changeText(screen.getByTestId('login-phone'), '29123456');

    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders in English', async () => {
    await i18next.changeLanguage('en');
    await renderLogin();

    expect(screen.getByRole('header', { name: 'Sign in' })).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: 'Get code' })).toBeOnTheScreen();
  });
});
