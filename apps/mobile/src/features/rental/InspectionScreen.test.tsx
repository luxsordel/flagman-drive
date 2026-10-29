import { act, fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { InspectionScreen } from './InspectionScreen';
import { RentalProvider } from './RentalProvider';
import { RentalStore } from './rentalStore';
import { fakeRentalApi, inspection, nearCar, ok, rental, serverNow } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

const mockPick = jest.fn();
const mockDiscard = jest.fn();
jest.mock('../kyc/photos', () => ({
  pickPhoto: (...args: unknown[]) => mockPick(...args) as unknown,
  discardPhoto: (uri: string) => {
    mockDiscard(uri);
  },
}));

async function renderScreen(photos = 0) {
  const fake = fakeRentalApi();
  fake.api.current.mockResolvedValue(ok(rental()));
  fake.api.inspection.mockResolvedValue(ok(inspection(photos)));
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  await store.restore();
  await render(
    <RentalProvider store={store}>
      <InspectionScreen />
    </RentalProvider>,
  );
  await act(async () => {
    await Promise.resolve();
  });
  return fake;
}

describe('InspectionScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockPick.mockResolvedValue({ kind: 'picked', uri: 'file:///cache/car.jpg' });
  });

  it('guides the client around the car', async () => {
    await renderScreen(2);

    expect(screen.getByText('Фото: 2 из 8')).toBeOnTheScreen();
    expect(screen.getByText('Следующий ракурс: слева')).toBeOnTheScreen();
    expect(screen.getByText('Нужно ещё фото: 2')).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: 'Завершить осмотр' })).toBeDisabled();
  });

  it('sends a photo as soon as it is taken, with progress, and frees the cache', async () => {
    const { uploads } = await renderScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Сделать фото' }));
    await act(() => {
      uploads[0]?.onProgress?.(0.5);
    });
    expect(screen.getByText('Загружаем фото… 50 %')).toBeOnTheScreen();

    await act(() => {
      uploads[0]?.finish(ok(inspection(1)));
    });

    expect(uploads[0]?.photoUri).toBe('file:///cache/car.jpg');
    expect(screen.getByText('Фото: 1 из 8')).toBeOnTheScreen();
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/car.jpg');
  });

  it('sends the same photo again after a network error', async () => {
    const { api, uploads } = await renderScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Сделать фото' }));
    await act(() => {
      uploads[0]?.finish({ ok: false, error: { kind: 'network' } });
    });

    expect(screen.getByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Отправить ещё раз' }));

    expect(api.uploadPhoto).toHaveBeenCalledTimes(2);
    expect(uploads[1]?.photoUri).toBe('file:///cache/car.jpg');
  });

  it('explains a camera without permission', async () => {
    mockPick.mockResolvedValue({ kind: 'cameraDenied' });
    await renderScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Сделать фото' }));

    expect(screen.getByRole('alert')).toHaveTextContent(/Нет доступа к камере/);
  });

  it('marks a damage with its side', async () => {
    const { api } = await renderScreen(4);
    api.addDamage.mockResolvedValue(
      ok(
        inspection(4, {
          damages: [{ id: 'd-1', side: 'rear', comment: 'Царапина', created_at: serverNow }],
        }),
      ),
    );

    await fireEvent.press(screen.getByRole('radio', { name: 'Сзади' }));
    await fireEvent.changeText(screen.getByLabelText('Что не так'), '  Царапина ');
    await fireEvent.press(screen.getByRole('button', { name: 'Добавить повреждение' }));

    expect(api.addDamage).toHaveBeenCalledWith('rental-1', 'rear', 'Царапина');
    expect(screen.getByText('Сзади: Царапина')).toBeOnTheScreen();
  });

  it('finishes the inspection and goes back to the reservation', async () => {
    const { api } = await renderScreen(4);
    api.submitInspection.mockResolvedValue(ok(inspection(4, { submitted_at: serverNow })));

    await fireEvent.press(screen.getByRole('button', { name: 'Завершить осмотр' }));

    expect(api.submitInspection).toHaveBeenCalledWith('rental-1');
    expect(mockRouter.dismissTo).toHaveBeenCalledWith('/rental');
  });
});
