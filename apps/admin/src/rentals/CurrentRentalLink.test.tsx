import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { ApiContext, type AdminApis } from '../api/context';
import ru from '../i18n/ru.json';
import { fakeRentalsApi, rentalItem } from '../test/rentals';
import { CurrentRentalLink } from './CurrentRentalLink';

function renderLink(rentals: ReturnType<typeof fakeRentalsApi>) {
  return render(
    <ApiContext value={{ rentals } as unknown as AdminApis}>
      <MemoryRouter>
        <CurrentRentalLink carId="car-1" />
      </MemoryRouter>
    </ApiContext>,
  );
}

describe('current rental link', () => {
  it('looks up the active rental of the car and links to it', async () => {
    const item = rentalItem({ status: 'in_trip' });
    const rentals = fakeRentalsApi({ items: [item] });
    renderLink(rentals);

    const link = await screen.findByRole('link', { name: ru.rentals.current });

    expect(link.getAttribute('href')).toBe(`/rentals/${item.id}`);
    expect(rentals.list).toHaveBeenCalledWith({
      carId: 'car-1',
      statuses: ['reserved', 'in_trip', 'parked'],
    });
  });

  it('shows nothing when the car has no rental', async () => {
    const rentals = fakeRentalsApi();
    renderLink(rentals);

    await Promise.resolve();
    expect(screen.queryByRole('link')).toBeNull();
  });
});
