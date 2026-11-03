import { act, fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { RentalProvider } from '../rental/RentalProvider';
import type { Fine } from '../rental/rentalApi';
import { RentalStore } from '../rental/rentalStore';
import { fakeRentalApi, nearCar, ok, serverNow } from '../rental/testing';
import { FinesScreen, TripFines } from './FinesScreen';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => {
  const { useEffect } = jest.requireActual<typeof import('react')>('react');
  return {
    useRouter: () => mockRouter,
    useFocusEffect: (effect: () => void) => {
      useEffect(effect, [effect]);
    },
  };
});

function fine(overrides: Partial<Fine> = {}): Fine {
  return {
    id: 'fine-1',
    rental_id: 'rental-1',
    fine_type_id: 'type-1',
    code: 'smoking',
    name_ru: 'Курение в салоне',
    name_en: 'Smoking in the car',
    amount: 10000,
    automatic: false,
    status: 'paid',
    comment: 'В салоне пахнет табаком',
    cancel_reason: null,
    refund_status: null,
    photos: [],
    created_at: '2026-11-03T10:00:00Z',
    cancelled_at: null,
    ...overrides,
  };
}

async function renderWith(fake: ReturnType<typeof fakeRentalApi>, element: React.ReactElement) {
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  await render(<RentalProvider store={store}>{element}</RentalProvider>);
  await act(async () => {
    await Promise.resolve();
  });
}

describe('FinesScreen', () => {
  beforeEach(() => {
    mockRouter.push.mockClear();
  });

  it('shows each fine with its reason, amount and status', async () => {
    const fake = fakeRentalApi();
    fake.api.fines.mockResolvedValue(ok([fine()]));
    await renderWith(fake, <FinesScreen />);

    expect(await screen.findByText('Курение в салоне')).toBeOnTheScreen();
    expect(screen.getByText(/^100,00\sBYN · Оплачен$/)).toBeOnTheScreen();
    expect(screen.getByText('В салоне пахнет табаком')).toBeOnTheScreen();
    expect(fake.api.fines).toHaveBeenCalledWith(undefined);
    expect(screen.queryByText('Оплатить')).toBeNull();
  });

  it('says so when there are no fines', async () => {
    const fake = fakeRentalApi();
    await renderWith(fake, <FinesScreen />);

    expect(await screen.findByText('Штрафов нет')).toBeOnTheScreen();
  });

  it('pays an unpaid fine and shows it paid', async () => {
    const fake = fakeRentalApi();
    fake.api.fines.mockResolvedValue(ok([fine({ status: 'unpaid' })]));
    fake.api.payFine.mockResolvedValue(ok(fine({ status: 'paid' })));
    await renderWith(fake, <FinesScreen />);

    await fireEvent.press(await screen.findByText('Оплатить'));

    expect(fake.api.payFine).toHaveBeenCalledWith('fine-1');
    expect(await screen.findByText(/Оплачен$/)).toBeOnTheScreen();
    expect(screen.queryByText('Оплатить')).toBeNull();
  });

  it('explains a refused payment', async () => {
    const fake = fakeRentalApi();
    fake.api.fines.mockResolvedValue(ok([fine({ status: 'unpaid' })]));
    fake.api.payFine.mockResolvedValue({ ok: false, error: { kind: 'network' } });
    await renderWith(fake, <FinesScreen />);

    await fireEvent.press(await screen.findByText('Оплатить'));

    expect(await screen.findByText('Оплатить')).toBeOnTheScreen();
    expect(screen.getByText(/Не оплачен$/)).toBeOnTheScreen();
  });

  it('opens the support chat about the trip to dispute a fine', async () => {
    const fake = fakeRentalApi();
    fake.api.fines.mockResolvedValue(ok([fine()]));
    await renderWith(fake, <FinesScreen />);

    await fireEvent.press(await screen.findByText('Оспорить в чате'));

    expect(mockRouter.push).toHaveBeenCalledWith({
      pathname: '/support',
      params: { rentalId: 'rental-1' },
    });
  });

  it('tells a cancelled fine with its reason and refund, and offers no dispute', async () => {
    const fake = fakeRentalApi();
    fake.api.fines.mockResolvedValue(
      ok([
        fine({
          status: 'cancelled',
          cancel_reason: 'Ошибка оператора',
          refund_status: 'succeeded',
        }),
      ]),
    );
    await renderWith(fake, <FinesScreen />);

    expect(await screen.findByText('Отменён: Ошибка оператора')).toBeOnTheScreen();
    expect(screen.getByText('Деньги возвращены на карту')).toBeOnTheScreen();
    expect(screen.queryByText('Оспорить в чате')).toBeNull();
  });

  it('opens a photo of the evidence on a tap only', async () => {
    const fake = fakeRentalApi();
    const photos = [{ id: 'photo-1', uploaded_at: '2026-11-03T10:01:00Z' }];
    fake.api.fines.mockResolvedValue(ok([fine({ photos })]));
    await renderWith(fake, <FinesScreen />);

    expect(fake.api.finePhotoUrl).not.toHaveBeenCalled();
    await fireEvent.press(await screen.findByText('Показать фото'));

    expect(fake.api.finePhotoUrl).toHaveBeenCalledWith('fine-1', 'photo-1');
    expect(await screen.findByLabelText('Фото к штрафу')).toBeOnTheScreen();
  });
});

describe('TripFines', () => {
  it('shows the fines of the trip on its receipt', async () => {
    const fake = fakeRentalApi();
    fake.api.fines.mockResolvedValue(ok([fine({ automatic: true, comment: null })]));
    await renderWith(fake, <TripFines rentalId="rental-1" />);

    expect(await screen.findByText('Штрафы за поездку')).toBeOnTheScreen();
    expect(screen.getByText('Начислен автоматически')).toBeOnTheScreen();
    expect(fake.api.fines).toHaveBeenCalledWith('rental-1');
  });

  it('shows nothing for a trip without fines', async () => {
    const fake = fakeRentalApi();
    await renderWith(fake, <TripFines rentalId="rental-1" />);

    expect(screen.queryByText('Штрафы за поездку')).toBeNull();
  });
});
