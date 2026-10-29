import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { Text as MockText } from 'react-native';

import { initI18n } from '../../i18n';
import { KycProvider } from '../kyc/KycProvider';
import type { KycStatus } from '../kyc/kycApi';
import { KycStore } from '../kyc/kycStore';
import { fakeKycApi, kycApplication, ok as kycOk } from '../kyc/testing';
import { RentalProvider } from '../rental/RentalProvider';
import { RentalStore } from '../rental/rentalStore';
import { fakeRentalApi, nearCar, ok as rentalOk, rental } from '../rental/testing';
import type { CarMapProps } from './carMapProps';
import { areaSettleMs } from './mapConfig';
import { MapProvider } from './MapProvider';
import { MapScreen } from './MapScreen';
import { MapStore } from './mapStore';
import { area, fakeMapApi, mapCar, mapZone, ok } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

const mockPlatform = { isExpoGo: false };
jest.mock('./platform', () => ({
  get isExpoGo() {
    return mockPlatform.isExpoGo;
  },
}));

const mockLocation = {
  // `off`: location services are switched off and the request itself fails.
  status: 'granted' as 'granted' | 'denied' | 'off',
  coords: { latitude: 53.91, longitude: 27.55 },
};
jest.mock('expo-location', () => ({
  Accuracy: { Balanced: 3 },
  PermissionStatus: { GRANTED: 'granted' },
  requestForegroundPermissionsAsync: () =>
    mockLocation.status === 'off'
      ? Promise.reject(new Error('Location services are disabled'))
      : Promise.resolve({ status: mockLocation.status }),
  watchPositionAsync: (_options: unknown, callback: (position: unknown) => void) => {
    callback({ coords: mockLocation.coords });
    return Promise.resolve({ remove: jest.fn() });
  },
}));

// The real map needs a native module or WebGL; the test drives it through its props.
let mockMapProps: CarMapProps | null = null;
jest.mock('./LazyCarMap', () => ({
  LazyCarMap: (props: CarMapProps) => {
    mockMapProps = props;
    return <MockText>{`map: ${String(props.cars.length)} cars`}</MockText>;
  },
}));

function mapProps(): CarMapProps {
  if (mockMapProps === null) throw new Error('the map is not rendered');
  return mockMapProps;
}

let mockRentals = fakeRentalApi();

async function renderMap({ kycStatus = 'pending' }: { kycStatus?: KycStatus } = {}) {
  const kyc = fakeKycApi();
  kyc.api.getApplication.mockResolvedValue(kycOk(kycApplication([], { status: kycStatus })));
  const api = fakeMapApi();
  api.listZones.mockResolvedValue(ok([mapZone()]));
  api.listCars.mockResolvedValue(ok([mapCar({ distance_m: 1540 })]));
  api.getCar.mockResolvedValue(ok(mapCar({ distance_m: 1540 })));
  await render(
    <KycProvider store={new KycStore(kyc.api)}>
      <MapProvider store={new MapStore(api)}>
        <RentalProvider
          store={
            new RentalStore(mockRentals.api, () =>
              Promise.resolve({ kind: 'located', location: nearCar }),
            )
          }
        >
          <MapScreen />
        </RentalProvider>
      </MapProvider>
    </KycProvider>,
  );
  return api;
}

async function showArea() {
  await screen.findByText(/^map:/);
  await act(async () => {
    mapProps().onAreaChange(area);
    await jest.advanceTimersByTimeAsync(areaSettleMs);
  });
}

describe('MapScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
    mockMapProps = null;
    mockRentals = fakeRentalApi();
    mockPlatform.isExpoGo = false;
    mockLocation.status = 'granted';
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('shows the cars and the zones of the visible area', async () => {
    const api = await renderMap();

    await showArea();

    expect(screen.getByText('map: 1 cars')).toBeOnTheScreen();
    expect(mapProps().zones).toEqual([mapZone()]);
    expect(api.listCars).toHaveBeenCalledWith(area, { lat: 53.91, lon: 27.55 }, 200);
  });

  it('flies to the client once their location is known', async () => {
    await renderMap();
    await screen.findByText(/^map:/);

    expect(mapProps().focus).toEqual({ lat: 53.91, lon: 27.55 });
    expect(mapProps().showUserLocation).toBe(true);
  });

  it('opens on Minsk without the location and says why there is no distance', async () => {
    mockLocation.status = 'denied';
    const api = await renderMap();

    await showArea();

    expect(mapProps().focus).toBeNull();
    expect(mapProps().showUserLocation).toBe(false);
    expect(api.listCars).toHaveBeenCalledWith(area, null, 200);
    expect(screen.getByText(/карта открыта на центре Минска/)).toBeOnTheScreen();
  });

  it('treats location services that are off as a refusal', async () => {
    mockLocation.status = 'off';
    await renderMap();

    await showArea();

    expect(mapProps().showUserLocation).toBe(false);
    expect(screen.getByText(/карта открыта на центре Минска/)).toBeOnTheScreen();
  });

  it('opens the card of a pressed car', async () => {
    await renderMap();
    await showArea();

    await act(async () => {
      mapProps().onCarPress('car-1');
      await jest.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText('Geely Geometry C')).toBeOnTheScreen();
    expect(screen.getByText('80 %')).toBeOnTheScreen();
    expect(screen.getByText('320 км')).toBeOnTheScreen();
    expect(screen.getByText('1,5 км')).toBeOnTheScreen();
    // Intl puts a no-break space between the amount and the currency.
    expect(screen.getByText(/^0,39\sBYN\/мин$/)).toBeOnTheScreen();
    expect(screen.getByText(/^0,12\sBYN\/мин$/)).toBeOnTheScreen();
    expect(mapProps().selectedId).toBe('car-1');

    await fireEvent.press(screen.getByRole('button', { name: 'Закрыть' }));

    expect(screen.queryByText('Geely Geometry C')).toBeNull();
    expect(mapProps().selectedId).toBeNull();
  });

  it('says when a pressed car is no longer available', async () => {
    const api = await renderMap();
    api.getCar.mockResolvedValue({ ok: false, error: { kind: 'carGone' } });
    await showArea();

    await act(async () => {
      mapProps().onCarPress('car-1');
      await jest.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText(/Эта машина больше недоступна/)).toBeOnTheScreen();
    expect(mapProps().cars).toEqual([]);
  });

  it('explains an empty area', async () => {
    const api = await renderMap();
    api.listCars.mockResolvedValue(ok([]));

    await showArea();

    expect(screen.getByText(/Здесь нет свободных машин/)).toBeOnTheScreen();
  });

  it('offers a retry after a network error', async () => {
    const api = await renderMap();
    api.listCars.mockResolvedValueOnce({ ok: false, error: { kind: 'network' } });
    await showArea();
    expect(screen.getByText(/Нет связи с сервером/)).toBeOnTheScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Повторить' }));
    await act(() => jest.advanceTimersByTimeAsync(0));

    expect(screen.queryByText(/Нет связи с сервером/)).toBeNull();
    expect(screen.getByText('map: 1 cars')).toBeOnTheScreen();
  });

  it('opens the documents with their status and the profile', async () => {
    await renderMap();

    await fireEvent.press(await screen.findByRole('button', { name: 'Документы: На проверке' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/kyc');

    await fireEvent.press(screen.getByRole('button', { name: 'Профиль' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/profile');
  });

  async function pressCar() {
    await showArea();
    await act(async () => {
      mapProps().onCarPress('car-1');
      await jest.advanceTimersByTimeAsync(0);
    });
  }

  it('reserves the car after a confirmation and opens the reservation', async () => {
    await renderMap({ kycStatus: 'approved' });
    mockRentals.api.reserve.mockResolvedValue(rentalOk(rental()));
    await pressCar();

    await fireEvent.press(screen.getByRole('button', { name: 'Забронировать' }));
    expect(mockRentals.api.reserve).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByRole('button', { name: 'Да, забронировать' }));

    expect(mockRentals.api.reserve).toHaveBeenCalledWith('car-1');
    expect(mockRouter.push).toHaveBeenCalledWith('/rental');
    expect(screen.getByText('Машина забронирована')).toBeOnTheScreen();
  });

  it('sends a client without approved documents to them instead', async () => {
    await renderMap({ kycStatus: 'pending' });
    await pressCar();

    expect(
      screen.getByText('Бронировать машины можно после одобрения документов'),
    ).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Забронировать' })).toBeNull();
    await fireEvent.press(screen.getByRole('button', { name: 'К документам' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/kyc');
  });

  it('explains a car taken meanwhile and refreshes the map', async () => {
    const api = await renderMap({ kycStatus: 'approved' });
    mockRentals.api.reserve.mockResolvedValue({ ok: false, error: { kind: 'carGone' } });
    await pressCar();
    const loads = api.listCars.mock.calls.length;

    await fireEvent.press(screen.getByRole('button', { name: 'Забронировать' }));
    await fireEvent.press(screen.getByRole('button', { name: 'Да, забронировать' }));

    expect(
      screen.getByText('Эта машина больше недоступна: её уже забронировали или ей нужна зарядка'),
    ).toBeOnTheScreen();
    expect(mockRouter.push).not.toHaveBeenCalledWith('/rental');
    expect(api.listCars.mock.calls.length).toBeGreaterThan(loads);
  });

  it('comes back to an active rental after a restart', async () => {
    mockRentals.api.current.mockResolvedValue(rentalOk(rental()));
    await renderMap({ kycStatus: 'approved' });

    expect(await screen.findByText('Машина забронирована')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Перейти' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/rental');
  });

  it('shows a notice instead of the map in Expo Go', async () => {
    mockPlatform.isExpoGo = true;
    const api = await renderMap();

    expect(screen.getByText('Карта работает только в dev-сборке')).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: 'Профиль' })).toBeOnTheScreen();
    expect(mockMapProps).toBeNull();
    expect(api.listZones).not.toHaveBeenCalled();
  });
});
