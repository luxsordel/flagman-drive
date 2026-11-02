import { fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { RentalBanner } from './RentalBanner';
import { RentalProvider } from './RentalProvider';
import { RentalStore } from './rentalStore';
import { fakeRentalApi, nearCar, paidRental, rental, serverNow } from './testing';

initI18n('ru');

function renderBanner(shown: Parameters<typeof RentalBanner>[0]['rental']) {
  const store = new RentalStore(
    fakeRentalApi().api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  const onReceipt = jest.fn();
  const view = render(
    <RentalProvider store={store}>
      <RentalBanner rental={shown} onOpen={jest.fn()} onReceipt={onReceipt} />
    </RentalProvider>,
  );
  return { onReceipt, view };
}

describe('RentalBanner', () => {
  it('shows what an expiry billed and opens its receipt', async () => {
    const { onReceipt, view } = renderBanner(
      paidRental({ status: 'cancelled', cancel_reason: 'expired' }),
    );
    await view;

    expect(
      screen.getByText('Время брони вышло: бронь отменена, машина снова свободна'),
    ).toBeOnTheScreen();
    expect(screen.getByText(/^Итого: 8,64\sBYN$/)).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Открыть чек' }));
    expect(onReceipt).toHaveBeenCalledWith('rental-1');
  });

  it('offers no receipt for a free expiry', async () => {
    const { view } = renderBanner(
      rental({ status: 'cancelled', cancel_reason: 'expired', ended_at: serverNow }),
    );
    await view;

    expect(screen.queryByRole('button', { name: 'Открыть чек' })).toBeNull();
    expect(screen.getByRole('button', { name: 'На карту' })).toBeOnTheScreen();
  });
});
