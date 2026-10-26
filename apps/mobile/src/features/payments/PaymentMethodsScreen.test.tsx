import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { PaymentMethodsScreen } from './PaymentMethodsScreen';
import { PaymentsProvider } from './PaymentsProvider';
import { binding, fakeBindingDeps, ok, paymentCard, started } from './testing';

initI18n('ru');

const mockRouter = { back: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

const visa = paymentCard();
const mastercard = paymentCard({
  id: 'card-2',
  brand: 'mastercard',
  last4: '4444',
  exp_month: 3,
  exp_year: 2031,
  is_default: false,
});

async function renderScreen(
  cards = [visa, mastercard],
  browser: 'returned' | 'closed' = 'returned',
) {
  const deps = fakeBindingDeps(browser);
  deps.api.listCards.mockResolvedValue(ok(cards));
  await render(
    <PaymentsProvider deps={deps}>
      <PaymentMethodsScreen />
    </PaymentsProvider>,
  );
  return deps;
}

const button = (name: string) => screen.getByRole('button', { name });

function removeButton(index: number) {
  const found = screen.getAllByRole('button', { name: 'Удалить' })[index];
  if (found === undefined) throw new Error(`no remove button ${String(index)}`);
  return found;
}

describe('PaymentMethodsScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('lists the cards by mask, expiry and the default one', async () => {
    await renderScreen();

    expect(await screen.findByText('Visa •••• 4242')).toBeOnTheScreen();
    expect(screen.getByText('Действует до 12/30')).toBeOnTheScreen();
    expect(screen.getByText('Mastercard •••• 4444')).toBeOnTheScreen();
    expect(screen.getByText('Действует до 03/31')).toBeOnTheScreen();
    expect(screen.getAllByText('Основная: с неё оплачиваются поездки')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Сделать основной' })).toHaveLength(1);
  });

  it('says when there are no cards', async () => {
    await renderScreen([]);
    expect(await screen.findByText('Карт пока нет')).toBeOnTheScreen();
  });

  it('marks an expired card and does not offer it as the default', async () => {
    await renderScreen([visa, { ...mastercard, expired: true }]);

    expect(
      await screen.findByText('Срок действия истёк: привяжите другую карту'),
    ).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Сделать основной' })).toBeNull();
  });

  it('binds a card on the provider page and shows it', async () => {
    const deps = await renderScreen([]);
    await screen.findByText('Карт пока нет');
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('succeeded', visa)));
    deps.api.listCards.mockResolvedValue(ok([visa]));

    await fireEvent.press(button('Привязать карту'));

    expect(await screen.findByText('Карта привязана')).toBeOnTheScreen();
    expect(await screen.findByText('Visa •••• 4242')).toBeOnTheScreen();
    expect(deps.openBrowser).toHaveBeenCalledWith(started.page_url, deps.returnUrl);
  });

  it('explains a declined card', async () => {
    const deps = await renderScreen([]);
    await screen.findByText('Карт пока нет');
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('declined')));

    await fireEvent.press(button('Привязать карту'));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Банк отклонил карту. Попробуйте другую',
    );
  });

  it('says the binding was cancelled when the page is closed', async () => {
    const deps = await renderScreen([], 'closed');
    await screen.findByText('Карт пока нет');
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('pending')));

    await fireEvent.press(button('Привязать карту'));

    expect(await screen.findByText('Привязка отменена')).toBeOnTheScreen();
  });

  it('explains too many attempts with the wait', async () => {
    const deps = await renderScreen([]);
    await screen.findByText('Карт пока нет');
    deps.api.startBinding.mockResolvedValue({
      ok: false,
      error: { kind: 'rateLimited', retryAfterSeconds: 600 },
    });

    await fireEvent.press(button('Привязать карту'));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Слишком много попыток. Повторите через 10 мин',
    );
    expect(deps.openBrowser).not.toHaveBeenCalled();
  });

  it('removes a card only after the client confirms', async () => {
    const deps = await renderScreen();
    await screen.findByText('Mastercard •••• 4444');
    deps.api.removeCard.mockResolvedValue(ok(null));

    await fireEvent.press(removeButton(1));
    expect(screen.getByText('Удалить карту Mastercard •••• 4444?')).toBeOnTheScreen();
    expect(deps.api.removeCard).not.toHaveBeenCalled();
    deps.api.listCards.mockResolvedValue(ok([visa]));
    await fireEvent.press(button('Да, удалить'));

    await waitFor(() => {
      expect(screen.queryByText('Mastercard •••• 4444')).toBeNull();
    });
    expect(deps.api.removeCard).toHaveBeenCalledWith('card-2');
  });

  it('keeps the card when the client changes their mind', async () => {
    const deps = await renderScreen();
    await screen.findByText('Visa •••• 4242');

    await fireEvent.press(removeButton(0));
    await fireEvent.press(button('Отмена'));

    expect(screen.queryByText('Удалить карту Visa •••• 4242?')).toBeNull();
    expect(deps.api.removeCard).not.toHaveBeenCalled();
  });

  it('makes another card the default', async () => {
    const deps = await renderScreen();
    await screen.findByText('Mastercard •••• 4444');
    deps.api.setDefaultCard.mockResolvedValue(ok({ ...mastercard, is_default: true }));

    await fireEvent.press(button('Сделать основной'));

    await waitFor(() => {
      expect(deps.api.setDefaultCard).toHaveBeenCalledWith('card-2');
    });
    await waitFor(() => {
      expect(deps.api.listCards).toHaveBeenCalledTimes(2);
    });
  });

  it('offers to retry when the cards do not load', async () => {
    const deps = fakeBindingDeps();
    deps.api.listCards
      .mockResolvedValueOnce({ ok: false, error: { kind: 'network' } })
      .mockResolvedValue(ok([visa]));
    await render(
      <PaymentsProvider deps={deps}>
        <PaymentMethodsScreen />
      </PaymentsProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Нет связи с сервером. Проверьте интернет',
    );
    await fireEvent.press(button('Повторить'));

    expect(await screen.findByText('Visa •••• 4242')).toBeOnTheScreen();
  });

  it('opens one provider page however fast the client taps', async () => {
    const deps = await renderScreen([]);
    await screen.findByText('Карт пока нет');
    deps.api.startBinding.mockResolvedValue(ok(started));
    deps.api.getBinding.mockResolvedValue(ok(binding('succeeded', visa)));
    deps.api.listCards.mockResolvedValue(ok([visa]));
    const bind = button('Привязать карту');

    // Both presses land before the button re-renders as disabled.
    await act(() => {
      void fireEvent.press(bind);
      void fireEvent.press(bind);
    });

    expect(await screen.findByText('Карта привязана')).toBeOnTheScreen();
    expect(deps.api.startBinding).toHaveBeenCalledTimes(1);
  });

  it('sends one request however fast the client taps', async () => {
    const deps = await renderScreen();
    await screen.findByText('Mastercard •••• 4444');
    deps.api.setDefaultCard.mockResolvedValue(ok({ ...mastercard, is_default: true }));
    const makeDefault = button('Сделать основной');

    // Both presses land before the button re-renders as disabled.
    await act(() => {
      void fireEvent.press(makeDefault);
      void fireEvent.press(makeDefault);
    });

    await waitFor(() => {
      expect(deps.api.listCards).toHaveBeenCalledTimes(2);
    });
    expect(deps.api.setDefaultCard).toHaveBeenCalledTimes(1);
  });

  it('explains a card that expired meanwhile', async () => {
    const deps = await renderScreen();
    await screen.findByText('Mastercard •••• 4444');
    deps.api.setDefaultCard.mockResolvedValue({ ok: false, error: { kind: 'expired' } });

    await fireEvent.press(button('Сделать основной'));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Срок действия карты истёк: её нельзя сделать основной',
    );
  });

  it('goes back', async () => {
    await renderScreen();
    await screen.findByText('Visa •••• 4242');

    await fireEvent.press(button('Назад'));

    expect(mockRouter.back).toHaveBeenCalled();
  });
});
