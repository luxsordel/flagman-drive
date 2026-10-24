import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { fakeKycReviewApi, kycApplication, kycQueueItem } from '../test/kyc';
import { renderAt } from '../test/render';

async function findRows(): Promise<HTMLElement[]> {
  const table = await screen.findByRole('table');
  return within(table).getAllByRole('row').slice(1);
}

afterEach(() => {
  vi.useRealTimers();
});

describe('KYC queue page', () => {
  it('lists the applications in the order of the API with Minsk time and the wait', async () => {
    vi.useFakeTimers({ now: new Date('2026-10-24T12:00:00Z'), toFake: ['Date'] });
    const kycReview = fakeKycReviewApi({
      queue: {
        items: [
          kycQueueItem({
            last_name: 'Petrov',
            first_name: 'Ivan',
            submitted_at: '2026-10-23T09:00:00Z',
          }),
          kycQueueItem({ last_name: null, first_name: null, submitted_at: '2026-10-24T11:35:00Z' }),
        ],
        total: 2,
      },
    });
    renderAt('/kyc', testStaff('operator'), { kycReview });

    const [first, second] = await findRows();
    expect(first?.textContent).toContain('Petrov Ivan');
    expect(first?.textContent).toContain('23.10.2026, 12:00:00');
    expect(first?.textContent).toContain('27 часов назад');
    expect(second?.textContent).toContain(ru.kyc.noName);
    expect(second?.textContent).toContain('25 минут назад');
    expect(screen.getByText('В очереди: 2')).toBeDefined();
  });

  it('opens the application from the queue', async () => {
    const item = kycQueueItem();
    const kycReview = fakeKycReviewApi({ queue: { items: [item], total: 1 } });
    renderAt('/kyc', testStaff('operator'), { kycReview });

    await userEvent.click(await screen.findByRole('link', { name: 'Petrov Ivan' }));

    expect(
      await screen.findByRole('heading', { level: 2, name: ru.kyc.profile.title }),
    ).toBeDefined();
    expect(kycReview.application).toHaveBeenCalledWith(item.id);
  });

  it('says when more applications wait than are shown', async () => {
    const kycReview = fakeKycReviewApi({ queue: { items: [kycQueueItem()], total: 120 } });
    renderAt('/kyc', testStaff('operator'), { kycReview });

    expect(await screen.findByText(/Показаны первые 1 /)).toBeDefined();
  });

  it('says when the queue is empty', async () => {
    renderAt('/kyc', testStaff('operator'));

    expect(await screen.findByText(ru.kyc.queue.empty)).toBeDefined();
  });

  it('offers to retry when the API is unreachable', async () => {
    const kycReview = fakeKycReviewApi({ queue: { items: [kycQueueItem()], total: 1 } });
    kycReview.queue.mockRejectedValueOnce(new NetworkError(new TypeError('offline')));
    renderAt('/kyc', testStaff('operator'), { kycReview });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(ru.kyc.queue.failed);
    await userEvent.click(within(alert).getByRole('button', { name: ru.kyc.retry }));

    expect(await findRows()).toHaveLength(1);
  });

  it('says when the role may not see the queue', async () => {
    const kycReview = fakeKycReviewApi();
    kycReview.queue.mockRejectedValue(new ApiError(403, 'forbidden'));
    renderAt('/kyc', testStaff('operator'), { kycReview });

    expect(await screen.findByText(ru.forbidden.text)).toBeDefined();
  });

  it('shows the decision message once: not again after a reload', async () => {
    const kycReview = fakeKycReviewApi();
    const { router } = renderAt(
      { pathname: '/kyc', state: { decided: 'approved' } },
      testStaff('operator'),
      { kycReview },
    );

    expect(await screen.findByText(ru.kyc.queue.approved)).toBeDefined();
    await waitFor(() => {
      expect(router.state.location.state).toBeNull();
    });
  });

  it('is open to an admin', async () => {
    const kycReview = fakeKycReviewApi({ queue: { items: [kycQueueItem()], total: 1 } });
    renderAt('/kyc', testStaff('admin'), { kycReview });

    expect(await findRows()).toHaveLength(1);
  });

  it('signs out when the session is over', async () => {
    const kycReview = fakeKycReviewApi();
    kycReview.queue.mockRejectedValue(new ApiError(401, 'access-token-invalid'));
    const { api } = renderAt('/kyc', testStaff('operator'), { kycReview });

    await waitFor(() => {
      expect(api.logout).toHaveBeenCalled();
    });
  });

  it.each(['/kyc', '/kyc/0199b000-1111-7000-8000-000000000001'])(
    'is closed to a technician at %s',
    async (path) => {
      const kycReview = fakeKycReviewApi();
      renderAt(path, testStaff('technician'), { kycReview });

      expect(await screen.findByRole('heading', { name: ru.forbidden.title })).toBeDefined();
      expect(kycReview.queue).not.toHaveBeenCalled();
      expect(kycReview.application).not.toHaveBeenCalled();
    },
  );
});

describe('KYC count in the menu', () => {
  it('shows how many applications wait and refreshes on the way between pages', async () => {
    const kycReview = fakeKycReviewApi({ queue: { items: [kycQueueItem()], total: 3 } });
    renderAt('/', testStaff('operator'), { kycReview });

    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    const link = await within(menu).findByRole('link', { name: `${ru.nav.kyc} 3` });
    expect(within(link).getByTitle('Ждут проверки: 3')).toBeDefined();
    expect(kycReview.queue).toHaveBeenLastCalledWith(1);

    kycReview.queue.mockResolvedValue({ items: [], total: 2 });
    await userEvent.click(link);

    expect(await within(menu).findByRole('link', { name: `${ru.nav.kyc} 2` })).toBeDefined();
  });

  it('refreshes the count after a decision', async () => {
    const user = userEvent.setup();
    const application = kycApplication();
    const kycReview = fakeKycReviewApi({
      queue: { items: [kycQueueItem({ id: application.id })], total: 3 },
      application,
    });
    renderAt(`/kyc/${application.id}`, testStaff('operator'), { kycReview });
    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    await within(menu).findByRole('link', { name: `${ru.nav.kyc} 3` });

    kycReview.queue.mockResolvedValue({ items: [], total: 2 });
    await user.type(await screen.findByLabelText(ru.kyc.reject.reason), 'Blurry');
    await user.click(screen.getByRole('button', { name: ru.kyc.reject.submit }));

    expect(await screen.findByText(ru.kyc.queue.rejected)).toBeDefined();
    expect(await within(menu).findByRole('link', { name: `${ru.nav.kyc} 2` })).toBeDefined();
  });

  it('refreshes the count when another operator turns out to have decided', async () => {
    const user = userEvent.setup();
    const application = kycApplication();
    const kycReview = fakeKycReviewApi({ queue: { items: [], total: 3 }, application });
    kycReview.reject.mockRejectedValueOnce(new ApiError(409, 'kyc-not-pending'));
    renderAt(`/kyc/${application.id}`, testStaff('operator'), { kycReview });
    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    await within(menu).findByRole('link', { name: `${ru.nav.kyc} 3` });

    kycReview.queue.mockResolvedValue({ items: [], total: 2 });
    await user.type(await screen.findByLabelText(ru.kyc.reject.reason), 'Blurry');
    await user.click(screen.getByRole('button', { name: ru.kyc.reject.submit }));

    expect(await within(menu).findByRole('link', { name: `${ru.nav.kyc} 2` })).toBeDefined();
  });

  it('shows no count when nothing waits', async () => {
    const kycReview = fakeKycReviewApi({ queue: { items: [], total: 0 } });
    renderAt('/fleet', testStaff('operator'), { kycReview });

    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    await waitFor(() => {
      expect(kycReview.queue).toHaveBeenCalled();
    });
    expect(within(menu).getByRole('link', { name: ru.nav.kyc })).toBeDefined();
  });

  it('shows no count when the API fails', async () => {
    const kycReview = fakeKycReviewApi();
    kycReview.queue.mockRejectedValue(new NetworkError(new TypeError('offline')));
    renderAt('/fleet', testStaff('operator'), { kycReview });

    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    await waitFor(() => {
      expect(kycReview.queue).toHaveBeenCalled();
    });
    expect(within(menu).getByRole('link', { name: ru.nav.kyc })).toBeDefined();
  });

  it('does not ask a technician, who has no such section', async () => {
    const kycReview = fakeKycReviewApi();
    renderAt('/', testStaff('technician'), { kycReview });

    await screen.findByRole('navigation', { name: ru.layout.menu });
    expect(kycReview.queue).not.toHaveBeenCalled();
  });
});
