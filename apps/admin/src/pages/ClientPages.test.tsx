import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { clientSummary, fakeClientsApi, staffClient } from '../test/clients';
import { renderAt } from '../test/render';

const operator = testStaff('operator');

afterEach(() => {
  vi.restoreAllMocks();
});

describe('clients list', () => {
  it('searches by what the operator typed', async () => {
    const clients = fakeClientsApi({ items: [clientSummary({ blocked: true })] });
    renderAt('/clients', operator, { clients });

    await userEvent.type(await screen.findByLabelText(ru.clients.search), 'Petr');
    await userEvent.click(screen.getByRole('button', { name: ru.clients.find }));

    expect(clients.list).toHaveBeenLastCalledWith({ search: 'Petr' });
    const row = (await screen.findByRole('link', { name: 'Petrov Ivan' })).closest('tr');
    expect(row?.textContent).toContain(ru.clients.yes);
  });

  it('is not open to a technician', async () => {
    renderAt('/clients', testStaff('technician'));

    expect(await screen.findByText(ru.forbidden.text)).toBeDefined();
  });
});

describe('client card', () => {
  it('shows the documents, the debt and a link to the rentals', async () => {
    const card = staffClient({ kyc_status: 'pending', debt: 1500 });
    renderAt(`/clients/${card.id}`, operator, { clients: fakeClientsApi({ card }) });

    expect(await screen.findByText(ru.clients.kycStatuses.pending)).toBeDefined();
    expect(screen.getByText(/15,00/)).toBeDefined();
    expect(screen.getByRole('link', { name: ru.clients.rentals }).getAttribute('href')).toBe(
      `/rentals?client=${card.id}`,
    );
  });

  it('blocks with a reason after a confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const clients = fakeClientsApi();
    const card = await clients.client('');
    renderAt(`/clients/${card.id}`, operator, { clients });

    await userEvent.type(await screen.findByLabelText(ru.clients.reason), 'Fraud');
    await userEvent.click(screen.getByRole('button', { name: ru.clients.block }));

    expect(clients.block).toHaveBeenCalledWith(card.id, 'Fraud');
    expect(await screen.findByRole('button', { name: ru.clients.unblock })).toBeDefined();
  });

  it('does nothing when the operator changes their mind', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const clients = fakeClientsApi();
    const card = await clients.client('');
    renderAt(`/clients/${card.id}`, operator, { clients });

    await userEvent.type(await screen.findByLabelText(ru.clients.reason), 'Fraud');
    await userEvent.click(screen.getByRole('button', { name: ru.clients.block }));

    expect(clients.block).not.toHaveBeenCalled();
  });
});
