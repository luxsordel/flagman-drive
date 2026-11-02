import { fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { CarCard, type Reservation } from './CarCard';
import { mapCar } from './testing';

initI18n('ru');

function reservation(overrides: Partial<Reservation> = {}): Reservation {
  return {
    kycApproved: true,
    blocked: false,
    reserving: false,
    onReserve: jest.fn(() => Promise.resolve(null)),
    onOpenDocuments: jest.fn(),
    onOpenSupport: jest.fn(),
    ...overrides,
  };
}

async function renderCard(given: Reservation) {
  const selected = { id: 'car-1', car: mapCar(), loading: false, error: null };
  await render(<CarCard selected={selected} reservation={given} onClose={jest.fn()} />);
}

describe('CarCard', () => {
  it('explains a block instead of offering the reservation', async () => {
    const given = reservation({ blocked: true });
    await renderCard(given);

    expect(screen.getByText(/Аккаунт заблокирован оператором/)).toBeOnTheScreen();
    expect(screen.queryByText('Забронировать')).toBeNull();
    await fireEvent.press(screen.getByText('Написать в поддержку'));
    expect(given.onOpenSupport).toHaveBeenCalled();
  });

  it('offers the reservation to a client who is not blocked', async () => {
    await renderCard(reservation());

    expect(screen.queryByText(/Аккаунт заблокирован/)).toBeNull();
    expect(screen.getByText('Забронировать')).toBeOnTheScreen();
  });
});
