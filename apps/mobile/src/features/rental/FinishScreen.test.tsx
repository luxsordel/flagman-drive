import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { Text as MockText } from 'react-native';

import { initI18n } from '../../i18n';
import { RentalProvider } from './RentalProvider';
import { checkStates, FinishScreen } from './FinishScreen';
import { RentalStore } from './rentalStore';
import { fakeRentalApi, nearCar, ok, paidRental, rental, serverNow } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));
jest.mock('../map/platform', () => ({ isExpoGo: false }));
jest.mock('../map/LazyCarMap', () => ({
  LazyCarMap: ({ zones }: { zones: unknown[] }) => (
    <MockText>{`map with ${String(zones.length)} zones`}</MockText>
  ),
}));
jest.mock('../map/MapProvider', () => ({
  useMapState: () => ({ zones: [{ id: 'zone-1' }] }),
}));

const trip = rental({
  status: 'in_trip',
  inspection_submitted: true,
  after_inspection_submitted: true,
  trip_started_at: '2026-10-26T11:50:00Z',
});

async function renderScreen(
  current = trip,
  before?: (api: ReturnType<typeof fakeRentalApi>['api'], store: RentalStore) => Promise<unknown>,
) {
  const fake = fakeRentalApi();
  fake.api.current.mockResolvedValue(ok(current));
  fake.api.get.mockResolvedValue(ok(current));
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  await store.restore();
  if (before) await before(fake.api, store);
  await render(
    <RentalProvider store={store}>
      <FinishScreen />
    </RentalProvider>,
  );
  return fake.api;
}

function check(name: string) {
  return screen.getByLabelText(new RegExp(`^${name}: `));
}

describe('checkStates', () => {
  it('marks the checks before the failed one as passed and the rest as open', () => {
    expect(checkStates(trip, { kind: 'ignitionOn' })).toEqual({
      inspection: 'passed',
      telemetry: 'passed',
      zone: 'passed',
      ignition: 'failed',
      doors: 'unchecked',
    });
  });

  it('knows only the inspection before the first attempt', () => {
    expect(checkStates({ ...trip, after_inspection_submitted: false }, null)).toEqual({
      inspection: 'failed',
      telemetry: 'unchecked',
      zone: 'unchecked',
      ignition: 'unchecked',
      doors: 'unchecked',
    });
  });

  it('ties a car that did not lock to the doors', () => {
    expect(checkStates(trip, { kind: 'lockFailed' }).doors).toBe('failed');
  });
});

describe('FinishScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('asks for the photos after the trip first', async () => {
    await renderScreen({ ...trip, after_inspection_submitted: false });

    expect(check('Фото машины после поездки')).toHaveTextContent(/пока нет/);
    expect(screen.getByRole('button', { name: 'Завершить' })).toBeDisabled();
    await fireEvent.press(screen.getByRole('button', { name: 'Сфотографировать машину' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/rental/inspection');
  });

  it('does not take a refused parking for a failed check', async () => {
    await renderScreen(trip, (api, store) => {
      api.park.mockResolvedValue({ ok: false, error: { kind: 'ignitionOn' } });
      return store.park();
    });

    expect(check('Зажигание выключено')).toHaveTextContent(/проверим/);
    expect(check('Свежие данные от машины')).toHaveTextContent(/проверим/);
  });

  it('shows the zones on the map when the car is outside them', async () => {
    const api = await renderScreen();
    api.finish.mockResolvedValue({ ok: false, error: { kind: 'outsideZone' } });

    await fireEvent.press(screen.getByRole('button', { name: 'Завершить' }));

    expect(check('Свежие данные от машины')).toHaveTextContent(/готово/);
    expect(check('Машина в зоне завершения')).toHaveTextContent(/пока нет/);
    expect(check('Зажигание выключено')).toHaveTextContent(/проверим следом/);
    expect(
      screen.getByText('Здесь завершить аренду нельзя: поставьте машину в зону завершения'),
    ).toBeOnTheScreen();
    expect(screen.getByText('map with 1 zones')).toBeOnTheScreen();
  });

  it.each([
    ['ignitionOn', 'Зажигание выключено', 'Сначала выключите зажигание'],
    ['doorsOpen', 'Двери закрыты, машина запирается', 'Сначала закройте все двери'],
  ] as const)('explains %s on its own line', async (kind, line, message) => {
    const api = await renderScreen();
    api.finish.mockResolvedValue({ ok: false, error: { kind } });

    await fireEvent.press(screen.getByRole('button', { name: 'Завершить' }));

    expect(check(line)).toHaveTextContent(/пока нет/);
    expect(screen.getByText(message)).toBeOnTheScreen();
  });

  it('goes to the receipt once the rental has ended', async () => {
    const api = await renderScreen();
    api.finish.mockResolvedValue(ok(paidRental()));

    await fireEvent.press(screen.getByRole('button', { name: 'Завершить' }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(api.finish).toHaveBeenCalledWith('rental-1');
    expect(api.debts).toHaveBeenCalled();
    expect(mockRouter.dismissTo).toHaveBeenCalledWith('/rental');
  });
});
