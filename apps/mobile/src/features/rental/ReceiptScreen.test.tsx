import { act, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { ReceiptScreen } from './ReceiptScreen';
import { RentalProvider } from './RentalProvider';
import { RentalStore } from './rentalStore';
import { fakeRentalApi, nearCar, ok, paidRental, serverNow } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => ({ id: 'rental-1' }),
}));

function setupStore() {
  const fake = fakeRentalApi();
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  return { fake, store };
}

describe('ReceiptScreen', () => {
  it('loads the rental of a tapped notification, also after a restart', async () => {
    const { fake, store } = setupStore();
    fake.api.get.mockResolvedValue(ok(paidRental()));

    await render(
      <RentalProvider store={store}>
        <ReceiptScreen />
      </RentalProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(fake.api.get).toHaveBeenCalledWith('rental-1');
    expect(screen.getByText(/^Итого: 8,64\sBYN$/)).toBeOnTheScreen();
  });

  it('shows the debt that is still owed', async () => {
    const { fake, store } = setupStore();
    fake.api.get.mockResolvedValue(
      ok(paidRental({ status: 'payment_failed', charge_status: 'failed' })),
    );
    fake.api.debts.mockResolvedValue(
      ok([{ rental_id: 'rental-1', amount: 450, ended_at: serverNow }]),
    );
    await store.loadDebts();

    await render(
      <RentalProvider store={store}>
        <ReceiptScreen />
      </RentalProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole('alert')).toHaveTextContent(/Оплата не прошла: 4,50\sBYN — долг/);
    expect(screen.getByRole('button', { name: 'Способы оплаты' })).toBeOnTheScreen();
  });

  it('explains a receipt that cannot be loaded', async () => {
    const { fake, store } = setupStore();
    fake.api.get.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await render(
      <RentalProvider store={store}>
        <ReceiptScreen />
      </RentalProvider>,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();
  });
});
