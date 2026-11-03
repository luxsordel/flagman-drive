import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import type { IncidentMapProps } from '../support/IncidentMap';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';
import { conversation, fakeSupportApi, incident, staffMessage } from '../test/support';

vi.mock('../support/IncidentMap', () => ({
  IncidentMap: ({ label }: IncidentMapProps) => <div role="img" aria-label={label} />,
}));

const operator = testStaff('operator');

describe('support queue', () => {
  it('shows the last message, the car and who took the conversation', async () => {
    const item = conversation({ car_plate: '1234 AB-7', assigned_staff_name: 'Anna', unread: 3 });
    renderAt('/support', operator, { support: fakeSupportApi({ queue: [item] }) });

    const row = (await screen.findByRole('link', { name: 'Petrov Ivan' })).closest('tr');
    expect(row?.textContent).toContain('The car smells of smoke');
    expect(row?.textContent).toContain('1234 AB-7');
    expect(row?.textContent).toContain('Anna');
    expect(row?.textContent).toContain('3');
  });

  it('calls a last message that is a photo a photo', async () => {
    const item = conversation({ last_message_text: null });
    renderAt('/support', operator, { support: fakeSupportApi({ queue: [item] }) });

    expect(await screen.findByText(ru.support.photo)).toBeDefined();
  });

  it('offers a retry when the queue does not load', async () => {
    const support = fakeSupportApi();
    support.queue.mockRejectedValueOnce(new ApiError(500));
    renderAt('/support', operator, { support });

    await userEvent.click(await screen.findByRole('button', { name: ru.support.retry }));

    expect(await screen.findByText(ru.support.empty)).toBeDefined();
  });

  it('is not open to a technician', async () => {
    renderAt('/support', testStaff('technician'));

    expect(await screen.findByText(ru.forbidden.text)).toBeDefined();
  });
});

describe('support conversation', () => {
  it('shows who answered and sends an answer with the last seen message', async () => {
    const card = conversation();
    const question = staffMessage();
    const answer = staffMessage({ author: 'staff', staff_name: 'Anna', text: 'On it' });
    const support = fakeSupportApi({ card, messages: [question, answer] });
    renderAt(`/support/${card.id}`, operator, { support });

    expect(await screen.findByText('On it')).toBeDefined();
    expect(screen.getByText(/Anna/)).toBeDefined();

    await userEvent.type(screen.getByLabelText(ru.support.answer), 'Thanks for waiting');
    await userEvent.click(screen.getByRole('button', { name: ru.support.send }));

    await waitFor(() => {
      expect(support.reply).toHaveBeenCalledWith(card.id, 'Thanks for waiting', answer.seq);
    });
    expect(await screen.findByText('Thanks for waiting')).toBeDefined();
  });

  it('keeps the answer when another operator has taken the conversation', async () => {
    const card = conversation();
    const support = fakeSupportApi({ card, messages: [staffMessage()] });
    support.reply.mockRejectedValueOnce(new ApiError(409, 'conversation-taken'));
    renderAt(`/support/${card.id}`, operator, { support });

    const field = await screen.findByLabelText(ru.support.answer);
    await userEvent.type(field, 'Hello');
    await userEvent.click(screen.getByRole('button', { name: ru.support.send }));

    expect(await screen.findByText(ru.support.problems.taken)).toBeDefined();
    expect((field as HTMLTextAreaElement).value).toBe('Hello');
  });

  it('shows nothing of one conversation on the screen of another', async () => {
    const card = conversation();
    const support = fakeSupportApi({ card, messages: [staffMessage({ text: 'First client' })] });
    const { router } = renderAt(`/support/${card.id}`, operator, { support });
    await screen.findByText('First client');

    support.messages.mockResolvedValue({
      conversation_id: 'another',
      status: 'open',
      items: [],
    });
    await act(async () => {
      await router.navigate('/support/another');
    });

    await waitFor(() => {
      expect(support.messages).toHaveBeenLastCalledWith('another', 0);
    });
    expect(screen.queryByText('First client')).toBeNull();
  });

  it('limits an answer to what the API takes', async () => {
    const card = conversation();
    renderAt(`/support/${card.id}`, operator, { support: fakeSupportApi({ card }) });

    const answer = await screen.findByLabelText(ru.support.answer);

    expect((answer as HTMLTextAreaElement).maxLength).toBe(2000);
  });

  it('asks for a photo link only when the operator opens the photo', async () => {
    const card = conversation();
    const photo = staffMessage({ kind: 'photo', text: null });
    const support = fakeSupportApi({ card, messages: [photo] });
    renderAt(`/support/${card.id}`, operator, { support });

    const show = await screen.findByRole('button', { name: ru.support.showPhoto });
    expect(support.messagePhotoUrl).not.toHaveBeenCalled();

    await userEvent.click(show);

    expect(await screen.findByRole('img', { name: ru.support.photo })).toBeDefined();
    expect(support.messagePhotoUrl).toHaveBeenCalledWith(card.id, photo.id);
  });

  it('links the client and the rental to their pages', async () => {
    const card = conversation({ rental_id: 'r-1', rental_status: 'in_trip' });
    renderAt(`/support/${card.id}`, operator, { support: fakeSupportApi({ card }) });

    const client = await screen.findByRole('link', { name: 'Petrov Ivan' });
    expect(client.getAttribute('href')).toBe(`/clients/${card.user_id}`);
    const rental = screen.getByRole('link', { name: ru.support.rentalStatus.in_trip });
    expect(rental.getAttribute('href')).toBe('/rentals/r-1');
  });

  it('offers no answer in a closed conversation', async () => {
    const card = conversation({ status: 'closed' });
    renderAt(`/support/${card.id}`, operator, { support: fakeSupportApi({ card }) });

    expect(await screen.findByText(ru.support.closed)).toBeDefined();
    expect(screen.queryByLabelText(ru.support.answer)).toBeNull();
  });
});

describe('incidents', () => {
  it('opens on the open ones, and shows the closed only when asked', async () => {
    const support = fakeSupportApi();
    renderAt('/incidents', operator, { support });

    await screen.findByText(ru.incidents.empty);
    expect(support.incidents).toHaveBeenCalledWith({});

    await userEvent.selectOptions(screen.getByLabelText(ru.incidents.status), ru.incidents.any);

    await waitFor(() => {
      expect(support.incidents).toHaveBeenLastCalledWith({ status: 'all' });
    });
  });

  it('marks urgent ones and filters by the address', async () => {
    const support = fakeSupportApi({
      incidents: [incident({ type: 'accident', priority: 0, car_plate: '1234 AB-7' })],
    });
    renderAt('/incidents?status=new', operator, { support });

    const link = await screen.findByRole('link', { name: ru.incidents.types.accident });
    expect(link.closest('tr')?.textContent).toContain(ru.incidents.urgent);
    expect(support.incidents).toHaveBeenCalledWith({ status: 'new' });

    await userEvent.selectOptions(
      screen.getByLabelText(ru.incidents.type),
      ru.incidents.types.damage,
    );

    await waitFor(() => {
      expect(support.incidents).toHaveBeenLastCalledWith({ status: 'new', type: 'damage' });
    });
  });

  it('loads the next page by its cursor', async () => {
    const support = fakeSupportApi();
    support.incidents.mockResolvedValueOnce({
      items: [incident({ type: 'damage' })],
      next_cursor: 'next',
    });
    support.incidents.mockResolvedValueOnce({
      items: [incident({ type: 'malfunction' })],
      next_cursor: null,
    });
    renderAt('/incidents', operator, { support });

    await userEvent.click(await screen.findByRole('button', { name: ru.incidents.more }));

    expect(await screen.findByRole('link', { name: ru.incidents.types.malfunction })).toBeDefined();
    expect(support.incidents).toHaveBeenLastCalledWith({}, 'next');
    expect(screen.getByRole('link', { name: ru.incidents.types.damage })).toBeDefined();
  });

  it('shows the place, the photos and the history', async () => {
    const card2 = incident({
      location: { lat: 53.9, lon: 27.56 },
      photos: [{ id: 'photo-1', uploaded_at: '2026-11-02T09:01:00Z' }],
      events: [
        {
          action: 'take',
          staff_id: 'staff-1',
          staff_name: 'Anna',
          comment: null,
          created_at: '2026-11-02T09:02:00Z',
        },
      ],
    });
    renderAt(`/incidents/${card2.id}`, operator, { support: fakeSupportApi({ card2 }) });

    expect(await screen.findByRole('img', { name: ru.incidents.place })).toBeDefined();
    expect(screen.getByRole('button', { name: ru.support.showPhoto })).toBeDefined();
    expect(screen.getByText(/Anna/)).toBeDefined();
  });

  it('links the client and the rental of an incident', async () => {
    const item = incident({ rental_id: 'r-2', rental_status: 'paid' });
    renderAt(`/incidents/${item.id}`, operator, { support: fakeSupportApi({ card2: item }) });

    const client = await screen.findByRole('link', { name: 'Petrov Ivan' });
    expect(client.getAttribute('href')).toBe(`/clients/${item.user_id}`);
    const rental = screen.getByRole('link', { name: ru.support.rentalStatus.paid });
    expect(rental.getAttribute('href')).toBe('/rentals/r-2');
  });

  it('needs a comment to resolve', async () => {
    const card2 = incident({ status: 'in_progress' });
    const support = fakeSupportApi({ card2 });
    renderAt(`/incidents/${card2.id}`, operator, { support });

    const resolve = await screen.findByRole('button', { name: ru.incidents.resolve });
    expect((resolve as HTMLButtonElement).disabled).toBe(true);

    await userEvent.type(screen.getByLabelText(ru.incidents.comment), 'Repaired');
    await userEvent.click(resolve);

    await waitFor(() => {
      expect(support.resolveIncident).toHaveBeenCalledWith(card2.id, 'Repaired');
    });
  });

  it('sends a car to service only from an incident in progress', async () => {
    const card2 = incident({ status: 'new', car_id: 'car-1', car_plate: '1234 AB-7' });
    renderAt(`/incidents/${card2.id}`, operator, { support: fakeSupportApi({ card2 }) });

    await screen.findByRole('button', { name: ru.support.take });

    expect(screen.queryByRole('button', { name: ru.incidents.sendToService })).toBeNull();
  });

  it('keeps the comment when a car goes to service', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const card2 = incident({ status: 'in_progress', car_id: 'car-1', car_plate: '1234 AB-7' });
    const support = fakeSupportApi({ card2 });
    renderAt(`/incidents/${card2.id}`, operator, { support });

    const comment = await screen.findByLabelText(ru.incidents.comment);
    await userEvent.type(comment, 'Bumper is cracked');
    await userEvent.click(screen.getByRole('button', { name: ru.incidents.sendToService }));

    await waitFor(() => {
      expect(support.sendToService).toHaveBeenCalledWith(card2.id);
    });
    expect((comment as HTMLTextAreaElement).value).toBe('Bumper is cracked');
  });

  it('says when a rental holds the car sent to service', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const card2 = incident({ status: 'in_progress', car_id: 'car-1', car_plate: '1234 AB-7' });
    const support = fakeSupportApi({ card2 });
    support.sendToService.mockRejectedValueOnce(new ApiError(409, 'car-transition-not-allowed'));
    renderAt(`/incidents/${card2.id}`, operator, { support });

    await userEvent.click(await screen.findByRole('button', { name: ru.incidents.sendToService }));

    expect(await screen.findByText(ru.incidents.problems.carBusy)).toBeDefined();
  });

  it('asks nothing of an incident already decided', async () => {
    const card2 = incident({ status: 'resolved' });
    renderAt(`/incidents/${card2.id}`, operator, { support: fakeSupportApi({ card2 }) });

    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByRole('button', { name: ru.incidents.resolve })).toBeNull();
    expect(screen.queryByRole('button', { name: ru.support.take })).toBeNull();
  });
});

describe('menu counters', () => {
  it('counts unread conversations and new incidents', async () => {
    renderAt('/kyc', operator, {
      support: fakeSupportApi({ unread: { conversations: 2, incidents: 5 } }),
    });

    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    expect(await within(menu).findByRole('link', { name: `${ru.nav.support} 2` })).toBeDefined();
    expect(within(menu).getByRole('link', { name: `${ru.nav.incidents} 5` })).toBeDefined();
  });

  it('does not ask for the counters as a technician', async () => {
    const support = fakeSupportApi();
    renderAt('/fleet', testStaff('technician'), { support });

    await screen.findByRole('navigation', { name: ru.layout.menu });
    expect(support.unread).not.toHaveBeenCalled();
  });
});
