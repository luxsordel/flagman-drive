import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { Text as MockText } from 'react-native';

import { initI18n } from '../../i18n';
import type { LocationFix } from '../map/location';
import { RentalProvider } from './RentalProvider';
import { RentalStore } from './rentalStore';
import { ReservationScreen } from './ReservationScreen';
import { fakeRentalApi, nearCar, ok, rental, serverNow } from './testing';

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
    // About 30 m south of the car.
    callback({ coords: { latitude: 53.902, longitude: 27.5615 } });
    return Promise.resolve({ remove: jest.fn() });
  },
}));

let mockFix: LocationFix = { kind: 'located', location: nearCar };

async function renderScreen(current = rental()) {
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

describe('ReservationScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers({ now: Date.parse(serverNow) });
    mockFix = { kind: 'located', location: nearCar };
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('shows the car, the plate, the distance and the free wait', async () => {
    await renderScreen();

    expect(screen.getByText('Geely Geometry C')).toBeOnTheScreen();
    expect(screen.getByText('Номер: 1234 AB-7')).toBeOnTheScreen();
    expect(screen.getByText('До машины: 30 м')).toBeOnTheScreen();
    expect(screen.getByText('Бесплатное ожидание: ещё 10:00')).toBeOnTheScreen();
  });

  it('counts down into the paid wait', async () => {
    await renderScreen();

    await act(async () => {
      await jest.advanceTimersByTimeAsync(10 * 60_000 + 1000);
    });

    expect(screen.getByText('Платное ожидание. Бронь отменится через 29:59')).toBeOnTheScreen();
  });

  it('asks the server when the time is over and shows the cancelled reservation', async () => {
    // Ends two seconds from now: the countdown runs to the end in two ticks.
    const api = await renderScreen(
      rental({ free_wait_until: serverNow, expires_at: '2026-10-26T12:00:02Z' }),
    );
    api.get.mockResolvedValue(ok(rental({ status: 'cancelled', cancel_reason: 'expired' })));

    await act(async () => {
      await jest.advanceTimersByTimeAsync(3000);
    });

    expect(
      screen.getByText('Время брони вышло: бронь отменена, машина снова свободна'),
    ).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'На карту' }));
    expect(mockRouter.dismissTo).toHaveBeenCalledWith('/');
  });

  it('leads to the inspection before the car can be opened', async () => {
    await renderScreen();

    expect(screen.queryByRole('button', { name: 'Открыть машину' })).toBeNull();
    await fireEvent.press(screen.getByRole('button', { name: 'Осмотреть машину' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/rental/inspection');
  });

  it('opens the car once inspected and starts the trip', async () => {
    const api = await renderScreen(rental({ inspection_submitted: true }));
    api.command.mockResolvedValue(
      ok(rental({ status: 'in_trip', inspection_submitted: true, trip_started_at: serverNow })),
    );

    await fireEvent.press(screen.getByRole('button', { name: 'Открыть машину' }));

    expect(api.command).toHaveBeenCalledWith('rental-1', 'open', nearCar);
    expect(
      screen.getByText('Поездка началась. Таймер и стоимость поездки появятся в следующей версии'),
    ).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Отменить бронь' })).toBeNull();
  });

  it('explains that the client is too far from the car', async () => {
    const api = await renderScreen(rental({ inspection_submitted: true }));
    api.command.mockResolvedValue({ ok: false, error: { kind: 'tooFar' } });

    await fireEvent.press(screen.getByRole('button', { name: 'Открыть машину' }));

    expect(
      screen.getByText(
        'Подойдите ближе: открыть или закрыть машину можно, только стоя рядом с ней',
      ),
    ).toBeOnTheScreen();
  });

  it('explains a refused location', async () => {
    mockFix = { kind: 'denied' };
    const api = await renderScreen(rental({ inspection_submitted: true }));

    await fireEvent.press(screen.getByRole('button', { name: 'Открыть машину' }));

    expect(api.command).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/Без доступа к местоположению/);
  });

  it('cancels the reservation only after a confirmation', async () => {
    const api = await renderScreen();
    api.cancel.mockResolvedValue(ok(rental({ status: 'cancelled', cancel_reason: 'client' })));

    await fireEvent.press(screen.getByRole('button', { name: 'Отменить бронь' }));
    expect(api.cancel).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByRole('button', { name: 'Да, отменить' }));

    expect(api.cancel).toHaveBeenCalledWith('rental-1');
    expect(screen.getByText('Бронь отменена')).toBeOnTheScreen();
  });
});
