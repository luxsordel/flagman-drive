import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';
import { fakeRentalsApi, rentalItem, staffRental } from '../test/rentals';

const operator = testStaff('operator');

afterEach(() => {
  vi.restoreAllMocks();
});

describe('rentals list', () => {
  it('shows each rental with its client, car and total', async () => {
    const item = rentalItem({ total: 1250, charge_status: 'failed' });
    renderAt('/rentals', operator, { rentals: fakeRentalsApi({ items: [item] }) });

    const row = (await screen.findByRole('link', { name: 'Petrov Ivan' })).closest('tr');
    expect(row?.textContent).toContain('1234 AB-7');
    expect(row?.textContent).toContain('12,50');
    expect(row?.textContent).toContain(ru.rentals.charge.failed);
  });

  it('takes the client and the status from the address', async () => {
    const rentals = fakeRentalsApi();
    renderAt('/rentals?client=abc&status=active', operator, { rentals });

    expect(await screen.findByText(ru.rentals.empty)).toBeDefined();
    expect(rentals.list).toHaveBeenCalledWith({
      status: 'active',
      statuses: ['reserved', 'in_trip', 'parked'],
      userId: 'abc',
    });
  });

  it('is not open to a technician', async () => {
    renderAt('/rentals', testStaff('technician'));

    expect(await screen.findByText(ru.forbidden.text)).toBeDefined();
  });
});

describe('rental card', () => {
  it('shows the receipt, the adjustments and the payments', async () => {
    const card = staffRental({
      adjustments: [
        {
          id: 'a1',
          amount: -200,
          created_at: '2026-11-02T10:00:00Z',
          comment: 'Late unlock',
          staff_id: 's1',
          staff_name: 'Anna',
        },
      ],
    });
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals: fakeRentalsApi({ card }) });

    expect(await screen.findByText('Late unlock')).toBeDefined();
    expect(screen.getByText(ru.rentals.operationKinds.capture)).toBeDefined();
    expect(screen.getByRole('link', { name: 'Petrov Ivan' }).getAttribute('href')).toBe(
      `/clients/${card.client.id}`,
    );
  });

  it('adjusts the total down with a comment after a confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const rentals = fakeRentalsApi();
    const card = await rentals.rental('');
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.type(await screen.findByLabelText(ru.rentals.amountRubles), '2,50');
    await userEvent.type(screen.getByLabelText(ru.rentals.comment), ' Late unlock ');
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.adjustSubmit }));

    expect(rentals.adjust).toHaveBeenCalledWith(
      card.rental.id,
      -250,
      'Late unlock',
      expect.stringMatching(/^[0-9a-f-]{36}$/),
    );
  });

  it('does not adjust without a comment', async () => {
    const card = staffRental();
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals: fakeRentalsApi({ card }) });

    await userEvent.type(await screen.findByLabelText(ru.rentals.amountRubles), '1');

    const submit = screen.getByRole('button', { name: ru.rentals.adjustSubmit });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it('tells when the payment is still settling', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const rentals = fakeRentalsApi();
    rentals.adjust.mockRejectedValueOnce(new ApiError(409, 'rental-settlement-pending'));
    const card = await rentals.rental('');
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.type(await screen.findByLabelText(ru.rentals.amountRubles), '1');
    await userEvent.type(screen.getByLabelText(ru.rentals.comment), 'Fee');
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.adjustSubmit }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.rentals.problems.pending);
  });

  it('force-finishes a trip and warns when the car did not lock', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const base = staffRental();
    const card = staffRental({ rental: { ...base.rental, status: 'in_trip', ended_at: null } });
    const rentals = fakeRentalsApi({ card });
    rentals.forceFinish.mockResolvedValueOnce({ rental: base, locked: false });
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.type(await screen.findByLabelText(ru.rentals.reason), 'Lost connection');
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.forceFinish }));

    expect(rentals.forceFinish).toHaveBeenCalledWith(card.rental.id, 'Lost connection');
    expect((await screen.findByRole('alert')).textContent).toBe(ru.rentals.notLocked);
    expect(screen.queryByRole('button', { name: ru.rentals.forceFinish })).toBeNull();
  });

  it('offers to send a refund the provider has not answered again', async () => {
    const base = staffRental();
    const lost = {
      id: 'r1',
      kind: 'refund' as const,
      amount: 100,
      status: 'failed' as const,
      parent_id: null,
      decline_reason: null,
      created_at: '2026-11-02T10:00:00Z',
    };
    const card = staffRental({ operations: [...base.operations, lost], owed: -100 });
    const rentals = fakeRentalsApi({ card });
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.click(await screen.findByRole('button', { name: ru.rentals.retryRefunds }));

    await waitFor(() => {
      expect(rentals.retryRefunds).toHaveBeenCalledWith(card.rental.id);
    });
    const money = screen.getByRole('heading', { name: ru.rentals.money }).closest('section');
    expect(within(money as HTMLElement).getByText(/1,00/)).toBeDefined();
  });
});
