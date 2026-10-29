import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { Text as MockText } from 'react-native';

import { initI18n } from '../../i18n';
import type { LocationFix } from '../map/location';
import { RentalProvider } from './RentalProvider';
import { RentalStore } from './rentalStore';
import { ReservationScreen } from './ReservationScreen';
import { fakeRentalApi, nearCar, ok, paidRental, rental, serverNow } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));
jest.mock('../map/platform', () => ({ isExpoGo: false }));
jest.mock('../map/LazyCarMap', () => ({
  LazyCarMap: () => <MockText>map</MockText>,
}));
jest.mock('expo-location', () => ({
  Accuracy: { Balanced: 3, High: 4 },
  PermissionStatus: { GRANTED: 'granted' },
  requestForegroundPermissionsAsync: () => Promise.resolve({ status: 'granted' }),
  watchPositionAsync: (_options: unknown, callback: (position: unknown) => void) => {
    callback({ coords: { latitude: 53.902, longitude: 27.5615 } });
    return Promise.resolve({ remove: jest.fn() });
  },
}));

const mockFix: LocationFix = { kind: 'located', location: nearCar };

// Ten minutes into the trip by the server's clock.
const trip = rental({
  status: 'in_trip',
  inspection_submitted: true,
  trip_started_at: '2026-10-26T11:50:00Z',
  cost: {
    lines: [{ kind: 'driving', minutes: 10, free_minutes: 0, per_minute: 39, amount: 390 }],
    total: 390,
  },
});

async function renderScreen(current = trip) {
  const fake = fakeRentalApi();
  fake.api.current.mockResolvedValue(ok(current));
  fake.api.get.mockResolvedValue(ok(current));
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve(mockFix),
    () => Date.parse(serverNow),
  );
  await render(
    <RentalProvider store={store}>
      <ReservationScreen />
    </RentalProvider>,
  );
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
  return fake.api;
}

describe('The trip on the rental screen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers({ now: Date.parse(serverNow) });
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('shows the time, the cost from the server and the charge', async () => {
    await renderScreen();

    expect(screen.getByText('Время в пути: 10:00')).toBeOnTheScreen();
    expect(screen.getByText(/^Стоимость сейчас: 3,90\sBYN$/)).toBeOnTheScreen();
    expect(screen.getByText('Заряд: 80 %')).toBeOnTheScreen();
    expect(screen.getByText('Запас хода: около 320 км')).toBeOnTheScreen();
    expect(screen.getByText(/^Движение: 0,39\sBYN\/мин$/)).toBeOnTheScreen();
  });

  it('keeps the timer running on its own', async () => {
    await renderScreen();

    await act(async () => {
      await jest.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByText('Время в пути: 10:05')).toBeOnTheScreen();
  });

  it('asks the server for the cost again while driving', async () => {
    const api = await renderScreen();
    api.get.mockResolvedValue(ok({ ...trip, cost: { lines: trip.cost.lines, total: 429 } }));

    await act(async () => {
      await jest.advanceTimersByTimeAsync(15_000);
    });

    expect(screen.getByText(/^Стоимость сейчас: 4,29\sBYN$/)).toBeOnTheScreen();
  });

  it('warns about low charge and range', async () => {
    await renderScreen({ ...trip, warnings: ['low_battery', 'low_range'] });

    expect(screen.getByText('Низкий заряд: спланируйте, где завершить поездку')).toBeOnTheScreen();
    expect(
      screen.getByText('Запаса хода может не хватить: двигайтесь к зоне завершения'),
    ).toBeOnTheScreen();
  });

  it('switches to parking and back', async () => {
    const api = await renderScreen();
    api.park.mockResolvedValue(ok({ ...trip, status: 'parked' }));
    api.resume.mockResolvedValue(ok(trip));

    await fireEvent.press(screen.getByRole('radio', { name: 'Стоянка' }));
    expect(api.park).toHaveBeenCalledWith('rental-1');
    expect(screen.getByText(/^Стоянка: 0,12\sBYN\/мин$/)).toBeOnTheScreen();

    await fireEvent.press(screen.getByRole('radio', { name: 'Движение' }));
    expect(api.resume).toHaveBeenCalledWith('rental-1');
  });

  it('leads to the finish', async () => {
    await renderScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Завершить аренду' }));

    expect(mockRouter.push).toHaveBeenCalledWith('/rental/finish');
  });

  it('keeps counting without the network', async () => {
    const api = await renderScreen();
    api.get.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await act(async () => {
      await jest.advanceTimersByTimeAsync(15_000);
    });

    expect(
      screen.getByText('Нет связи: время идёт, стоимость обновится, когда связь вернётся'),
    ).toBeOnTheScreen();
    expect(screen.getByText('Время в пути: 10:15')).toBeOnTheScreen();
  });

  it('shows the receipt of a paid trip', async () => {
    await renderScreen(paidRental());

    expect(screen.getByText('Чек')).toBeOnTheScreen();
    expect(screen.getByText(/^Движение: 20 мин × 0,39\sBYN = 7,80\sBYN$/)).toBeOnTheScreen();
    expect(screen.getByText('Бесплатное ожидание: 15 мин')).toBeOnTheScreen();
    expect(screen.getByText(/^Итого: 8,64\sBYN$/)).toBeOnTheScreen();
    expect(screen.getByText('Карта VISA •••• 4242')).toBeOnTheScreen();
    expect(screen.getByText('Оплачено')).toBeOnTheScreen();
  });

  it('explains a failed payment and lets the client pay the debt', async () => {
    const failed = paidRental({ status: 'payment_failed', charge_status: 'failed' });
    const api = await renderScreen(failed);
    api.payDebt.mockResolvedValue(ok(paidRental()));

    expect(screen.getByRole('alert')).toHaveTextContent(/Оплата не прошла: 8,64\sBYN — долг/);
    await fireEvent.press(screen.getByRole('button', { name: 'Оплатить сейчас' }));

    expect(api.payDebt).toHaveBeenCalledWith('rental-1');
    expect(screen.getByText('Оплачено')).toBeOnTheScreen();
  });
});
