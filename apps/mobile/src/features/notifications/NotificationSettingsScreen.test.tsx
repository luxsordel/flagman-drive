import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { NotificationSettingsScreen } from './NotificationSettingsScreen';
import { NotificationsProvider } from './NotificationsContext';
import {
  fakeNotificationsDeps,
  fakePushDeps,
  granted,
  notAsked,
  ok,
  preferences,
  refused,
} from './testing';

initI18n('ru');

const mockRouter = { back: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

const deviceTexts = {
  enabled: 'Уведомления на этом телефоне включены',
  askable: 'Уведомления на этом телефоне выключены',
  blocked: 'Уведомления запрещены в настройках телефона',
  unsupported: 'В браузере push-уведомлений нет: они приходят в приложении на iOS и Android',
} as const;

/** Renders and waits until the phone's push state is known, so no update lands after a test. */
async function renderScreen(
  push = fakePushDeps(),
  device: keyof typeof deviceTexts = 'enabled',
  deps = fakeNotificationsDeps(push),
) {
  if (deps.api.getPreferences.getMockImplementation() === undefined) {
    deps.api.getPreferences.mockResolvedValue(ok(preferences()));
  }
  await render(
    <NotificationsProvider deps={deps}>
      <NotificationSettingsScreen />
    </NotificationsProvider>,
  );
  await screen.findByText(deviceTexts[device]);
  return deps;
}

const toggle = (name: string) => screen.getByRole('switch', { name });

describe('NotificationSettingsScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('shows every category, with trip and payment locked on', async () => {
    await renderScreen();

    expect(await screen.findByRole('switch', { name: 'Бронь' })).not.toBeDisabled();
    expect(toggle('Поездка')).toBeDisabled();
    expect(toggle('Оплата')).toBeDisabled();
    expect(screen.getAllByText('Нельзя отключить: это о вашей поездке и деньгах')).toHaveLength(2);
  });

  it('switches the reservation reminders off', async () => {
    const deps = await renderScreen();
    deps.api.updatePreferences.mockResolvedValue(ok(preferences(['reservation'])));

    await fireEvent(await screen.findByRole('switch', { name: 'Бронь' }), 'valueChange', false);

    await waitFor(() => {
      expect(toggle('Бронь')).toHaveProp('value', false);
    });
    expect(deps.api.updatePreferences).toHaveBeenCalledWith({ reservation: false });
  });

  it('explains a failed change and keeps the switch as it was', async () => {
    const deps = await renderScreen();
    deps.api.updatePreferences.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await fireEvent(await screen.findByRole('switch', { name: 'Бронь' }), 'valueChange', false);

    expect(await screen.findByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();
    expect(toggle('Бронь')).toHaveProp('value', true);
  });

  it('offers to retry when the preferences do not load', async () => {
    const deps = fakeNotificationsDeps();
    deps.api.getPreferences
      .mockResolvedValueOnce({ ok: false, error: { kind: 'network' } })
      .mockResolvedValue(ok(preferences()));
    await renderScreen(deps.push, 'enabled', deps);

    await fireEvent.press(await screen.findByRole('button', { name: 'Повторить' }));

    expect(await screen.findByRole('switch', { name: 'Бронь' })).toBeOnTheScreen();
  });

  it('says pushes are on for this phone', async () => {
    await renderScreen(fakePushDeps(granted));

    expect(screen.getByText(deviceTexts.enabled)).toBeOnTheScreen();
  });

  it('asks for the permission only on a tap', async () => {
    const push = fakePushDeps(notAsked);
    await renderScreen(push, 'askable');

    expect(push.requestPermission).not.toHaveBeenCalled();

    await fireEvent.press(screen.getByRole('button', { name: 'Включить уведомления' }));

    expect(await screen.findByText('Уведомления на этом телефоне включены')).toBeOnTheScreen();
    expect(push.requestPermission).toHaveBeenCalledTimes(1);
  });

  it('sends a refused permission to the system settings', async () => {
    const deps = await renderScreen(fakePushDeps(refused), 'blocked');

    await fireEvent.press(
      await screen.findByRole('button', { name: 'Открыть настройки телефона' }),
    );

    expect(deps.openSystemSettings).toHaveBeenCalledTimes(1);
  });

  it('explains that the web gets no pushes', async () => {
    await renderScreen(fakePushDeps(granted, null), 'unsupported');

    expect(screen.getByText(deviceTexts.unsupported)).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Включить уведомления' })).toBeNull();
  });
});
