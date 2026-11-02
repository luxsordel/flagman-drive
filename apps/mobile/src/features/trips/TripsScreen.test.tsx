import { act, fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { RentalProvider } from '../rental/RentalProvider';
import type { HistoryItem } from '../rental/rentalApi';
import { RentalStore } from '../rental/rentalStore';
import { fakeRentalApi, nearCar, ok, serverNow } from '../rental/testing';
import { TripsScreen } from './TripsScreen';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => {
  const { useEffect } = jest.requireActual<typeof import('react')>('react');
  return {
    useRouter: () => mockRouter,
    // The screen is focused while it is rendered in these tests.
    useFocusEffect: (effect: () => void) => {
      useEffect(effect, [effect]);
    },
  };
});

function trip(overrides: Partial<HistoryItem> = {}): HistoryItem {
  return {
    id: 'rental-1',
    status: 'paid',
    car: { id: 'car-1', brand: 'Geely', model: 'Geometry C', plate: '1234 AB-7', photo_url: null },
    reserved_at: '2026-10-26T11:00:00Z',
    trip_started_at: '2026-10-26T11:05:00Z',
    ended_at: '2026-10-26T11:30:00Z',
    total: 864,
    charge_status: 'paid',
    ...overrides,
  };
}

async function renderTrips(fake: ReturnType<typeof fakeRentalApi>) {
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  await render(
    <RentalProvider store={store}>
      <TripsScreen />
    </RentalProvider>,
  );
  await act(async () => {
    await Promise.resolve();
  });
}

describe('TripsScreen', () => {
  beforeEach(() => {
    mockRouter.push.mockClear();
  });

  it('lists the trips with their cost and opens the receipt of one', async () => {
    const fake = fakeRentalApi();
    fake.api.history.mockResolvedValue(ok({ items: [trip()], next_cursor: null }));
    await renderTrips(fake);

    expect(await screen.findByText('В пути 25 мин')).toBeOnTheScreen();
    expect(screen.getByText(/^8,64\sBYN$/)).toBeOnTheScreen();
    await fireEvent.press(screen.getByText('Geely Geometry C · 1234 AB-7'));

    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: '/rental/receipt',
      params: { id: 'rental-1' },
    });
  });

  it('marks a debt and opens the current rental on its own screen', async () => {
    const fake = fakeRentalApi();
    const items = [
      trip({ id: 'now', status: 'in_trip', ended_at: null, charge_status: null }),
      trip({ id: 'owed', status: 'payment_failed', charge_status: 'failed' }),
    ];
    fake.api.history.mockResolvedValue(ok({ items, next_cursor: null }));
    await renderTrips(fake);

    expect(await screen.findByText('Не оплачено: откройте, чтобы оплатить')).toBeOnTheScreen();
    await fireEvent.press(screen.getByText('Идёт сейчас'));
    expect(mockRouter.push).toHaveBeenCalledWith('/rental');
  });

  it('loads the next page by its cursor', async () => {
    const fake = fakeRentalApi();
    fake.api.history
      .mockResolvedValueOnce(ok({ items: [trip()], next_cursor: 'c-1' }))
      .mockResolvedValueOnce(ok({ items: [trip({ id: 'older', total: 100 })], next_cursor: null }));
    await renderTrips(fake);

    await fireEvent.press(await screen.findByText('Показать ещё'));

    expect(fake.api.history).toHaveBeenLastCalledWith('c-1');
    expect(await screen.findByText(/^1,00\sBYN$/)).toBeOnTheScreen();
    expect(screen.queryByText('Показать ещё')).toBeNull();
  });

  it('says when there were no trips', async () => {
    await renderTrips(fakeRentalApi());

    expect(await screen.findByText('Поездок пока не было')).toBeOnTheScreen();
  });
});
