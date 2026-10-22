import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import { initI18n } from '../../i18n';
import { CodeScreen } from './CodeScreen';
import { AuthProvider } from './session/AuthProvider';
import { json, problem, testSession, tokens } from './session/testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), back: jest.fn() };
jest.mock('expo-router', () => {
  const { Text } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    useRouter: () => mockRouter,
    Redirect: ({ href }: { href: string }): ReactNode => <Text>{`redirect:${href}`}</Text>,
  };
});

const accepted = json({ expires_in: 300, resend_after: 60 }, 202);

async function renderCode({ requested = true } = {}) {
  const setup = testSession();
  setup.api.on('/auth/otp/request', accepted);
  if (requested) await setup.session.requestCode('+375291234567');
  await render(
    <AuthProvider session={setup.session}>
      <CodeScreen />
    </AuthProvider>,
  );
  return setup;
}

describe('CodeScreen', () => {
  beforeEach(() => {
    // Promises still settle on their own; only the countdown clock is ours.
    jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask', 'setImmediate'] });
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  it('sends the user back to the phone step when no code was requested', async () => {
    await renderCode({ requested: false });

    expect(screen.getByText('redirect:/sign-in')).toBeOnTheScreen();
  });

  it('signs in as soon as all six digits are in, e.g. from SMS autofill', async () => {
    const { api, session } = await renderCode();
    api.on('/auth/otp/verify', json(tokens(1)));

    expect(screen.getByText('Отправили код на +375 29 123-45-67')).toBeOnTheScreen();
    await fireEvent.changeText(screen.getByTestId('code-input'), '123456');

    await waitFor(() => {
      expect(session.getStatus()).toBe('signedIn');
    });
    expect(api.calls.at(-1)).toMatchObject({
      path: '/auth/otp/verify',
      body: { phone: '+375291234567', code: '123456' },
    });
  });

  it('keeps only digits and waits for the full code', async () => {
    const { api } = await renderCode();

    await fireEvent.changeText(screen.getByTestId('code-input'), '12-34');

    expect(screen.getByTestId('code-input')).toHaveDisplayValue('1234');
    expect(screen.getByRole('button', { name: 'Войти' })).toBeDisabled();
    expect(api.calls.map((call) => call.path)).not.toContain('/auth/otp/verify');
  });

  it('clears a wrong code and explains what happened', async () => {
    const { api } = await renderCode();
    api.on('/auth/otp/verify', problem('otp-invalid', 400));

    await fireEvent.changeText(screen.getByTestId('code-input'), '000000');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Неверный код. Проверьте и введите ещё раз',
    );
    expect(screen.getByTestId('code-input')).toHaveDisplayValue('');
  });

  it('asks for a new code once the old one stops working', async () => {
    const { api } = await renderCode();
    api.on('/auth/otp/verify', problem('otp-expired', 400));

    await fireEvent.changeText(screen.getByTestId('code-input'), '000000');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Код больше не действует. Запросите новый',
    );
  });

  it('offers a new code after the resend timeout and restarts the timer', async () => {
    const { api } = await renderCode();
    expect(screen.getByText('Новый код можно запросить через 1:00')).toBeOnTheScreen();

    await act(() => {
      jest.advanceTimersByTime(59_000);
    });
    expect(screen.getByText('Новый код можно запросить через 0:01')).toBeOnTheScreen();

    await act(() => {
      jest.advanceTimersByTime(1_000);
    });
    await fireEvent.press(screen.getByRole('button', { name: 'Отправить код ещё раз' }));

    expect(await screen.findByText('Новый код можно запросить через 1:00')).toBeOnTheScreen();
    expect(api.calls.filter((call) => call.path === '/auth/otp/request')).toHaveLength(2);
  });

  it('goes back to change the number', async () => {
    await renderCode();

    await fireEvent.press(screen.getByRole('button', { name: 'Изменить номер' }));

    expect(mockRouter.back).toHaveBeenCalled();
  });
});
