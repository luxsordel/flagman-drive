import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';
import { demoPrices, fakeTariffsApi, modelTariff, tariffVersion } from '../test/tariffs';

const plain = (text: string | null | undefined): string => (text ?? '').replace(/\s/g, ' ');

async function findRow(name: string): Promise<HTMLElement> {
  const table = await screen.findByRole('table');
  const row = within(table)
    .getAllByRole('row')
    .find((candidate) => candidate.textContent.includes(name));
  if (!row) throw new Error(`no row for ${name}`);
  return row;
}

async function openModel(name: string): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole('button', { name: `BYD ${name}` }));
  return screen.findByRole('region', { name: `BYD ${name}` });
}

describe('tariffs page', () => {
  it('lists every model with its prices in rubles and the moment they apply from', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin'), modelTariff('Seal', null)]);
    renderAt('/tariffs', testStaff('operator'), { tariffs });

    const dolphin = await findRow('BYD Dolphin');
    expect(plain(dolphin.textContent)).toContain('0,39 BYN');
    expect(plain(dolphin.textContent)).toContain('30,00 BYN');
    expect(dolphin.textContent).toContain('15 мин');
    expect(dolphin.textContent).toContain('26.10.2026, 12:00:00');
    expect((await findRow('BYD Seal')).textContent).toContain(ru.tariffs.none);
  });

  it('shows an operator the history but no form', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin')]);
    renderAt('/tariffs', testStaff('operator'), { tariffs });

    const panel = await openModel('Dolphin');

    expect(
      await within(panel).findByRole('heading', { name: ru.tariffs.history.title }),
    ).toBeDefined();
    expect(within(panel).queryByRole('button', { name: ru.tariffs.form.save })).toBeNull();
    expect(tariffs.versions).toHaveBeenCalled();
  });

  it('saves a new version after the admin confirms it', async () => {
    const item = modelTariff('Dolphin');
    const tariffs = fakeTariffsApi([item]);
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const panel = await openModel('Dolphin');
    const driving = within(panel).getByLabelText(new RegExp(ru.tariffs.fields.driving_per_minute));
    expect((driving as HTMLInputElement).value).toBe('0.39');
    await userEvent.clear(driving);
    await userEvent.type(driving, '0,45');
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.form.save }));

    const confirm = within(panel).getByRole('group', { name: ru.tariffs.confirm.title });
    expect(confirm.textContent).toContain(ru.tariffs.confirm.editText);
    expect(tariffs.update).not.toHaveBeenCalled();
    await userEvent.click(
      within(confirm).getByRole('button', { name: ru.tariffs.confirm.confirm }),
    );

    expect(tariffs.update).toHaveBeenCalledWith(item.tariff_id, 1, {
      ...demoPrices,
      driving_per_minute: 45,
    });
    expect((await screen.findByRole('status')).textContent).toContain('BYD Dolphin');
    expect(plain((await findRow('BYD Dolphin')).textContent)).toContain('0,45 BYN');
  });

  it('goes back to editing without saving', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin')]);
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const panel = await openModel('Dolphin');
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.form.save }));
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.confirm.back }));

    expect(within(panel).getByRole('button', { name: ru.tariffs.form.save })).toBeDefined();
    expect(tariffs.update).not.toHaveBeenCalled();
  });

  it('creates the tariff of a model without one', async () => {
    const item = modelTariff('Seal', null);
    const tariffs = fakeTariffsApi([item]);
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const panel = await openModel('Seal');
    expect(within(panel).getByRole('heading', { name: ru.tariffs.form.createTitle })).toBeDefined();
    for (const [field, value] of [
      ['driving_per_minute', '0,39'],
      ['parked_per_minute', '0,12'],
      ['waiting_per_minute', '0,12'],
      ['hold_amount', '30'],
    ] as const) {
      await userEvent.type(
        within(panel).getByLabelText(new RegExp(ru.tariffs.fields[field])),
        value,
      );
    }
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.form.save }));
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.confirm.confirm }));

    expect(tariffs.create).toHaveBeenCalledWith(item.model.id, demoPrices);
  });

  it('points at the fields to fix before asking to confirm', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin')]);
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const panel = await openModel('Dolphin');
    const hold = within(panel).getByLabelText(new RegExp(ru.tariffs.fields.hold_amount));
    await userEvent.clear(hold);
    await userEvent.type(hold, 'много');
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.form.save }));

    expect(hold.getAttribute('aria-invalid')).toBe('true');
    expect(within(panel).getByText(ru.tariffs.form.problems.format)).toBeDefined();
    expect(within(panel).queryByRole('group', { name: ru.tariffs.confirm.title })).toBeNull();
  });

  it('asks to reload when someone else saved the tariff meanwhile', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin')]);
    tariffs.update.mockRejectedValueOnce(new ApiError(409, 'tariff-changed'));
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const panel = await openModel('Dolphin');
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.form.save }));
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.confirm.confirm }));

    const alert = await within(panel).findByRole('alert');
    expect(alert.textContent).toContain(ru.tariffs.errors.changed);
    await userEvent.click(within(alert).getByRole('button', { name: ru.tariffs.reload }));
    expect(tariffs.list).toHaveBeenCalledTimes(2);
  });

  it('explains a refused save', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin')]);
    tariffs.update.mockRejectedValueOnce(new ApiError(403, 'forbidden'));
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const panel = await openModel('Dolphin');
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.form.save }));
    await userEvent.click(within(panel).getByRole('button', { name: ru.tariffs.confirm.confirm }));

    expect((await within(panel).findByRole('alert')).textContent).toContain(
      ru.tariffs.errors.forbidden,
    );
  });

  it('shows the versions of a tariff, newest first', async () => {
    const item = modelTariff('Dolphin', tariffVersion({ number: 2 }));
    const tariffs = fakeTariffsApi([item]);
    tariffs.versions.mockResolvedValueOnce([
      tariffVersion({ number: 2, prices: { ...demoPrices, driving_per_minute: 45 } }),
      tariffVersion({ number: 1, valid_from: '2026-10-20T09:00:00Z' }),
    ]);
    renderAt('/tariffs', testStaff('operator'), { tariffs });

    const panel = await openModel('Dolphin');

    const history = await within(panel).findByRole('region', { name: ru.tariffs.history.title });
    const rows = within(history).getAllByRole('row').slice(1);
    expect(rows.map((row) => row.firstElementChild?.textContent)).toEqual(['2', '1']);
    expect(plain(rows[0]?.textContent)).toContain('0,45 BYN');
    expect(rows[1]?.textContent).toContain('20.10.2026');
    expect(tariffs.versions).toHaveBeenCalledWith(item.tariff_id);
  });

  it('offers to retry when the API is unreachable', async () => {
    const tariffs = fakeTariffsApi([modelTariff('Dolphin')]);
    tariffs.list.mockRejectedValueOnce(new NetworkError(new TypeError('offline')));
    renderAt('/tariffs', testStaff('admin'), { tariffs });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(ru.tariffs.failed);
    await userEvent.click(within(alert).getByRole('button', { name: ru.tariffs.retry }));

    expect(await findRow('BYD Dolphin')).toBeDefined();
  });

  it('is closed to technicians', async () => {
    renderAt('/tariffs', testStaff('technician'));

    expect(await screen.findByRole('heading', { name: ru.forbidden.title })).toBeDefined();
  });
});
