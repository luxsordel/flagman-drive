import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { fakeFinesApi, fine, fineType } from '../test/fines';
import { renderAt } from '../test/render';
import { fakeRentalsApi, staffRental } from '../test/rentals';

const operator = testStaff('operator');
const admin = testStaff('admin');

afterEach(() => {
  vi.restoreAllMocks();
});

describe('fines page', () => {
  it('lists charged fines with their client, rental and status', async () => {
    const item = fine({ status: 'unpaid' });
    const fines = fakeFinesApi({ items: [item] });
    renderAt('/fines', operator, { fines });

    const row = (await screen.findByRole('link', { name: 'Petrov Ivan' })).closest('tr');
    expect(row?.textContent).toContain(ru.fines.statuses.unpaid);
    expect(row?.textContent).toContain('100,00');
    expect(row?.textContent).toContain('Olga');
    expect(within(row as HTMLElement).getByRole('link', { name: '1234 AB-7' })).toBeDefined();
  });

  it('takes the filters from the address', async () => {
    const fines = fakeFinesApi();
    const client = '0199c000-aaaa-7000-8000-000000000001';
    renderAt(`/fines?status=cancelled&client=${client}&from=2026-11-01&to=2026-11-02`, operator, {
      fines,
    });

    expect(await screen.findByText(ru.fines.empty)).toBeDefined();
    expect(fines.list).toHaveBeenCalledWith({
      status: 'cancelled',
      userId: client,
      from: '2026-11-01',
      to: '2026-11-02',
    });
  });

  it('ignores filters that are not one', async () => {
    const fines = fakeFinesApi();
    renderAt('/fines?status=lost&client=nobody&from=2026-02-30', operator, { fines });

    expect(await screen.findByText(ru.fines.empty)).toBeDefined();
    expect(fines.list).toHaveBeenCalledWith({});
  });

  it('shows the catalog to an operator without a way to change it', async () => {
    const fines = fakeFinesApi({ types: [fineType()] });
    renderAt('/fines', operator, { fines });

    expect(await screen.findByRole('cell', { name: 'Курение в салоне' })).toBeDefined();
    expect(screen.queryByRole('button', { name: ru.fines.catalog.edit })).toBeNull();
    expect(screen.queryByRole('form', { name: ru.fines.catalog.addTitle })).toBeNull();
  });

  it('lets an admin save new terms based on the version shown', async () => {
    const type = fineType({ number: 3 });
    const fines = fakeFinesApi({ types: [type] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderAt('/fines', admin, { fines });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: ru.fines.catalog.edit }));
    const amount = screen.getAllByLabelText(ru.fines.catalog.amount)[0] as HTMLInputElement;
    await user.clear(amount);
    await user.type(amount, '120,50');
    await user.click(screen.getByRole('button', { name: ru.fines.catalog.save }));

    await waitFor(() => {
      expect(fines.updateType).toHaveBeenCalledWith(type.fine_type_id, 3, {
        name_ru: type.name_ru,
        name_en: type.name_en,
        amount: 12050,
      });
    });
  });

  it('tells the admin a newer version was saved meanwhile', async () => {
    const fines = fakeFinesApi({ types: [fineType()] });
    fines.updateType.mockRejectedValueOnce(new ApiError(409, 'fine-type-changed'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderAt('/fines', admin, { fines });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: ru.fines.catalog.edit }));
    await user.click(screen.getByRole('button', { name: ru.fines.catalog.save }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.fines.catalog.problems.changed);
  });

  it('lets an admin add a manual type', async () => {
    const fines = fakeFinesApi();
    renderAt('/fines', admin, { fines });
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: ru.fines.catalog.addTitle });

    await user.type(within(form).getByLabelText(ru.fines.catalog.code), 'pets');
    await user.type(within(form).getByLabelText(ru.fines.catalog.nameRu), 'Животные');
    await user.type(within(form).getByLabelText(ru.fines.catalog.nameEn), 'Pets');
    await user.type(within(form).getByLabelText(ru.fines.catalog.amount), '40');
    await user.click(within(form).getByRole('button', { name: ru.fines.catalog.add }));

    await waitFor(() => {
      expect(fines.createType).toHaveBeenCalledWith('pets', {
        name_ru: 'Животные',
        name_en: 'Pets',
        amount: 4000,
      });
    });
  });

  it('is not in the menu of a technician', async () => {
    renderAt('/fines', testStaff('technician'));

    expect(await screen.findByRole('heading', { name: ru.forbidden.title })).toBeDefined();
  });
});

describe('fines of a rental', () => {
  it('lists the fines with their history and evidence', async () => {
    const charged = fine({ photos: [{ id: 'p-1', uploaded_at: '2026-11-03T10:01:00Z' }] });
    const fines = fakeFinesApi({ items: [charged] });
    renderAt('/rentals/r-1', operator, { fines, rentals: fakeRentalsApi() });

    expect(await screen.findByText('Курение в салоне')).toBeDefined();
    expect(fines.list).toHaveBeenCalledWith({ rentalId: expect.any(String) as string });
    expect(screen.getByText('Smoked', { selector: 'p' })).toBeDefined();
    expect(screen.getByRole('button', { name: ru.support.showPhoto })).toBeDefined();
  });

  it('charges a manual fine with a comment and its photos after a confirmation', async () => {
    const smoking = fineType();
    const fines = fakeFinesApi({
      types: [smoking, fineType({ code: 'low_battery', kind: 'automatic' })],
    });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const card = staffRental();
    renderAt(`/rentals/${card.rental.id}`, operator, {
      fines,
      rentals: fakeRentalsApi({ card }),
    });
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: ru.fines.chargeTitle });

    const select = within(form).getByLabelText(ru.fines.type);
    await within(select).findByRole('option', { name: /Курение/ });
    // The automatic type is the system's to charge: an operator does not get to pick it.
    expect(within(select).getAllByRole('option')).toHaveLength(2);
    await user.selectOptions(select, smoking.fine_type_id);
    await user.type(within(form).getByLabelText(ru.fines.comment), '  Smoked  ');
    const photo = new File(['jpeg'], 'smoke.jpg', { type: 'image/jpeg' });
    await user.upload(within(form).getByLabelText(ru.fines.photos), photo);
    await user.click(within(form).getByRole('button', { name: ru.fines.chargeSubmit }));

    await waitFor(() => {
      expect(fines.addPhoto).toHaveBeenCalledWith(expect.any(String), photo);
    });
    expect(confirm).toHaveBeenCalled();
    expect(fines.charge).toHaveBeenCalledWith(card.rental.id, smoking.fine_type_id, 'Smoked');
  });

  it('charges nothing when the operator says no', async () => {
    const smoking = fineType();
    const fines = fakeFinesApi({ types: [smoking] });
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderAt('/rentals/r-1', operator, { fines, rentals: fakeRentalsApi() });
    const user = userEvent.setup();
    const form = await screen.findByRole('form', { name: ru.fines.chargeTitle });
    await within(form).findByRole('option', { name: /Курение/ });

    await user.selectOptions(within(form).getByLabelText(ru.fines.type), smoking.fine_type_id);
    await user.type(within(form).getByLabelText(ru.fines.comment), 'Smoked');
    await user.click(within(form).getByRole('button', { name: ru.fines.chargeSubmit }));

    expect(fines.charge).not.toHaveBeenCalled();
  });

  it('offers no fine to a trip still going on', async () => {
    const base = staffRental();
    const card = staffRental({ rental: { ...base.rental, status: 'in_trip', ended_at: null } });
    renderAt('/rentals/r-1', operator, { rentals: fakeRentalsApi({ card }) });

    expect(await screen.findByText(ru.fines.noneForRental)).toBeDefined();
    expect(screen.queryByRole('form', { name: ru.fines.chargeTitle })).toBeNull();
  });

  it('cancels a fine with a reason', async () => {
    const charged = fine();
    const fines = fakeFinesApi({ items: [charged] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderAt('/rentals/r-1', operator, { fines, rentals: fakeRentalsApi() });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: ru.fines.cancel }));
    await user.type(screen.getByLabelText(ru.fines.reason), 'Wrong car');
    await user.click(screen.getByRole('button', { name: ru.fines.cancelSubmit }));

    await waitFor(() => {
      expect(fines.cancel).toHaveBeenCalledWith(charged.id, 'Wrong car');
    });
    expect(
      await screen.findByText(ru.fines.cancelledBecause.replace('{{reason}}', 'Wrong car')),
    ).toBeDefined();
  });
});
