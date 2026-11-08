import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
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

  it('says the total of a rental still going on is not known yet', async () => {
    const item = rentalItem({ status: 'in_trip', total: 0, charge_status: null });
    renderAt('/rentals', operator, { rentals: fakeRentalsApi({ items: [item] }) });

    const row = (await screen.findByRole('link', { name: 'Petrov Ivan' })).closest('tr');
    expect(row?.textContent).toContain(ru.rentals.ongoing);
    expect(row?.textContent).not.toContain('0,00');
  });

  it('tells a refused filter apart from a failure to load', async () => {
    const rentals = fakeRentalsApi();
    rentals.list.mockRejectedValueOnce(new ApiError(422));
    renderAt('/rentals?from=2026-11-02&to=2026-11-01', operator, { rentals });

    expect((await screen.findByRole('alert')).textContent).toBe(ru.load.rejected);
  });

  it('loads the next page once however often it is pressed', async () => {
    const rentals = fakeRentalsApi({ items: [rentalItem()] });
    rentals.list.mockResolvedValueOnce({ items: [rentalItem()], next_cursor: 'c-1' });
    let release: () => void = () => undefined;
    rentals.list.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ items: [rentalItem({ id: 'older' })], next_cursor: null });
          };
        }),
    );
    renderAt('/rentals', operator, { rentals });

    const more = await screen.findByRole('button', { name: ru.rentals.more });
    await userEvent.click(more);
    await userEvent.click(more);
    release();

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: ru.rentals.more })).toBeNull();
    });
    expect(rentals.list).toHaveBeenCalledTimes(2);
  });

  it('keeps the rows when the next page fails and drops a page of an older filter', async () => {
    const rentals = fakeRentalsApi();
    rentals.list.mockResolvedValueOnce({ items: [rentalItem()], next_cursor: 'c-1' });
    rentals.list.mockRejectedValueOnce(new ApiError(500));
    const { router } = renderAt('/rentals', operator, { rentals });

    await userEvent.click(await screen.findByRole('button', { name: ru.rentals.more }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.load.moreFailed);
    expect(screen.getByRole('link', { name: 'Petrov Ivan' })).toBeDefined();

    let release: () => void = () => undefined;
    rentals.list.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => {
            resolve({ items: [rentalItem({ id: 'stale' })], next_cursor: null });
          };
        }),
    );
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.more }));
    rentals.list.mockResolvedValueOnce({ items: [], next_cursor: null });
    await act(async () => {
      await router.navigate('/rentals?status=paid');
    });
    expect(await screen.findByText(ru.rentals.empty)).toBeDefined();
    await act(async () => {
      release();
      await Promise.resolve();
    });

    expect(screen.queryByRole('link', { name: 'Petrov Ivan' })).toBeNull();
    expect(screen.getByText(ru.rentals.empty)).toBeDefined();
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

  it('sends a retry with the same key and a changed form with a new one', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const rentals = fakeRentalsApi();
    rentals.adjust.mockRejectedValueOnce(new NetworkError(new Error('offline')));
    const card = await rentals.rental('');
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });
    const submit = async () => {
      await userEvent.click(screen.getByRole('button', { name: ru.rentals.adjustSubmit }));
    };
    const keys = () => rentals.adjust.mock.calls.map((call) => call[3]);

    await userEvent.type(await screen.findByLabelText(ru.rentals.amountRubles), '1');
    await userEvent.type(screen.getByLabelText(ru.rentals.comment), 'Fee');
    await submit();
    await screen.findByRole('alert');
    await submit();
    await userEvent.type(screen.getByLabelText(ru.rentals.amountRubles), '2');
    await userEvent.type(screen.getByLabelText(ru.rentals.comment), 'Fee');
    await submit();

    const [lost, retried, next] = keys();
    expect(retried).toBe(lost);
    expect(next).not.toBe(retried);
  });

  it('names a reason the API refused for a forced finish', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const base = staffRental();
    const card = staffRental({ rental: { ...base.rental, status: 'in_trip', ended_at: null } });
    const rentals = fakeRentalsApi({ card });
    rentals.forceFinish.mockRejectedValueOnce(new ApiError(422));
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.type(await screen.findByLabelText(ru.rentals.reason), 'Lost');
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.forceFinish }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.rentals.problems.reason);
  });

  it('says the car is still answering instead of asking to reload', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const base = staffRental();
    const card = staffRental({ rental: { ...base.rental, status: 'in_trip', ended_at: null } });
    const rentals = fakeRentalsApi({ card });
    rentals.forceFinish.mockRejectedValueOnce(new ApiError(409, 'rental-command-in-progress'));
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.type(await screen.findByLabelText(ru.rentals.reason), 'Lost');
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.forceFinish }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.rentals.problems.busy);
  });

  it('tells how long to wait when the staff command limit refuses a forced finish', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const base = staffRental();
    const card = staffRental({ rental: { ...base.rental, status: 'in_trip', ended_at: null } });
    const rentals = fakeRentalsApi({ card });
    rentals.forceFinish.mockRejectedValueOnce(new ApiError(429, 'rate-limited', 40));
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals });

    await userEvent.type(await screen.findByLabelText(ru.rentals.reason), 'Lost');
    await userEvent.click(screen.getByRole('button', { name: ru.rentals.forceFinish }));

    expect((await screen.findByRole('alert')).textContent).toBe(
      ru.rentals.problems.rateLimited.replace('{{seconds}}', '40'),
    );
  });

  it('offers to finish a refund when money is owed back and no refund was made', async () => {
    const card = staffRental({ owed: -100 });
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals: fakeRentalsApi({ card }) });

    expect(await screen.findByRole('button', { name: ru.rentals.retryRefunds })).toBeDefined();
  });

  it('tells a rental that is not there', async () => {
    const rentals = fakeRentalsApi();
    rentals.rental.mockRejectedValueOnce(new ApiError(404, 'rental-not-found'));
    renderAt('/rentals/missing', operator, { rentals });

    expect((await screen.findByRole('alert')).textContent).toBe(ru.load.notFound);
  });

  it('offers no adjustment of a reservation cancelled in its free wait', async () => {
    const base = staffRental();
    const card = staffRental({
      rental: { ...base.rental, status: 'cancelled', receipt: null, charge_status: null },
      operations: [],
      intervals: [],
    });
    renderAt(`/rentals/${card.rental.id}`, operator, { rentals: fakeRentalsApi({ card }) });

    expect(await screen.findByText(ru.rentals.noTrip)).toBeDefined();
    expect(screen.queryByRole('button', { name: ru.rentals.adjustSubmit })).toBeNull();
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
