import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { fakeKycReviewApi, kycApplication } from '../test/kyc';
import { renderAt } from '../test/render';

const kinds = ru.kyc.documents.kinds;

function openApplication(application = kycApplication()) {
  const kycReview = fakeKycReviewApi({ application });
  const rendered = renderAt(`/kyc/${application.id}`, testStaff('operator'), { kycReview });
  return { ...rendered, kycReview, application };
}

const approveForm = () => screen.getByRole('form', { name: ru.kyc.approve.title });
const rejectForm = () => screen.getByRole('form', { name: ru.kyc.reject.title });

afterEach(() => {
  vi.useRealTimers();
});

describe('KYC application page', () => {
  it('shows the profile data to check the documents against', async () => {
    openApplication(kycApplication({ first_name: 'Ivan', last_name: 'Petrov', age_years: 36 }));

    expect(await screen.findByRole('heading', { level: 1, name: 'Petrov Ivan' })).toBeDefined();
    const profile = screen.getByRole('region', { name: ru.kyc.profile.title });
    expect(profile.textContent).toContain('17 мая 1990 г.');
    expect(profile.textContent).toContain('36');
    expect(profile.textContent).toContain('23.10.2026, 12:00:00');
    expect(profile.textContent).toContain(ru.kyc.status.pending);
  });

  it('marks the profile fields the client has not filled in', async () => {
    openApplication(
      kycApplication({ first_name: null, last_name: null, birth_date: null, age_years: null }),
    );

    expect(await screen.findByRole('heading', { level: 1, name: ru.kyc.noName })).toBeDefined();
    const profile = screen.getByRole('region', { name: ru.kyc.profile.title });
    expect(within(profile).getAllByText(ru.kyc.profile.notGiven)).toHaveLength(4);
  });

  it('suggests rejecting an application sent with an empty profile', async () => {
    const user = userEvent.setup();
    const { kycReview, application } = openApplication(
      kycApplication({ last_name: null, birth_date: null, age_years: null }),
    );

    const hint = await screen.findByRole('region', { name: ru.kyc.profileMissing.title });
    expect(hint.textContent).toContain(ru.kyc.profileMissing.text);
    expect(screen.queryByRole('form', { name: ru.kyc.approve.title })).toBeNull();

    await user.click(
      within(rejectForm()).getByRole('button', { name: ru.kyc.profileMissing.useReason }),
    );
    expect(within(rejectForm()).getByLabelText(ru.kyc.reject.reason)).toHaveProperty(
      'value',
      ru.kyc.profileMissing.reason,
    );
    await user.click(within(rejectForm()).getByRole('button', { name: ru.kyc.reject.submit }));

    expect(await screen.findByText(ru.kyc.queue.rejected)).toBeDefined();
    expect(kycReview.reject).toHaveBeenCalledWith(application.id, ru.kyc.profileMissing.reason);
  });

  it('offers no ready reason when the profile is filled in', async () => {
    openApplication();
    await screen.findByRole('heading', { level: 1 });

    expect(approveForm()).toBeDefined();
    expect(screen.queryByRole('region', { name: ru.kyc.profileMissing.title })).toBeNull();
    expect(
      within(rejectForm()).queryByRole('button', { name: ru.kyc.profileMissing.useReason }),
    ).toBeNull();
  });

  it('shows every uploaded photo by its own link, without a referrer', async () => {
    const { kycReview, application } = openApplication(
      kycApplication({
        documents: [
          { kind: 'license_front', uploaded_at: '2026-10-23T08:50:00Z' },
          { kind: 'selfie_with_license', uploaded_at: '2026-10-23T08:50:00Z' },
        ],
      }),
    );

    const photo = await screen.findByRole('img', { name: kinds.license_front });
    expect(photo.getAttribute('src')).toBe('http://storage.test/license_front.jpg?link=1');
    expect(photo.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(await screen.findByRole('img', { name: kinds.selfie_with_license })).toBeDefined();
    expect(screen.getAllByText(ru.kyc.documents.missing)).toHaveLength(2);
    expect(kycReview.documentUrl.mock.calls).toEqual([
      [application.id, 'license_front'],
      [application.id, 'selfie_with_license'],
    ]);
  });

  it('offers to load a photo again when its link fails', async () => {
    const { kycReview } = openApplication(
      kycApplication({ documents: [{ kind: 'passport', uploaded_at: '2026-10-23T08:50:00Z' }] }),
    );
    kycReview.documentUrl.mockRejectedValueOnce(new ApiError(429, 'rate-limited'));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(ru.kyc.documents.rateLimited);
    await userEvent.click(within(alert).getByRole('button', { name: ru.kyc.documents.reload }));

    expect(await screen.findByRole('img', { name: kinds.passport })).toBeDefined();
  });

  it('offers to load a photo again when the image does not open', async () => {
    openApplication(
      kycApplication({ documents: [{ kind: 'passport', uploaded_at: '2026-10-23T08:50:00Z' }] }),
    );

    fireEvent.error(await screen.findByRole('img', { name: kinds.passport }));
    await userEvent.click(await screen.findByRole('button', { name: ru.kyc.documents.reload }));

    const photo = await screen.findByRole('img', { name: kinds.passport });
    expect(photo.getAttribute('src')).toBe('http://storage.test/passport.jpg?link=2');
  });

  it('opens a photo large, zooms, rotates and closes with Escape', async () => {
    const user = userEvent.setup();
    openApplication();

    const open = await screen.findByRole('button', {
      name: `Открыть крупно: ${kinds.license_back}`,
    });
    await user.click(open);

    const viewer = screen.getByRole('dialog', { name: kinds.license_back });
    const image = within(viewer).getByRole('img', { name: kinds.license_back });
    expect(
      within(viewer)
        .getByRole('button', { name: ru.kyc.viewer.zoomOut })
        .getAttribute('aria-disabled'),
    ).toBe('true');
    await user.click(within(viewer).getByRole('button', { name: ru.kyc.viewer.zoomIn }));
    await user.click(within(viewer).getByRole('button', { name: ru.kyc.viewer.rotate }));
    expect(image.parentElement?.style.width).toBe('150%');
    expect(image.style.transform).toBe('translate(-50%, -50%) rotate(90deg)');
    // Russian puts a no-break space before the percent sign.
    expect(within(viewer).getByRole('status').textContent).toBe('150\u00a0%');

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(open);
  });

  it('makes the page under the open photo inert', async () => {
    const user = userEvent.setup();
    openApplication();

    await user.click(
      await screen.findByRole('button', { name: `Открыть крупно: ${kinds.passport}` }),
    );
    const viewer = screen.getByRole('dialog', { name: kinds.passport });

    const page = approveForm().closest('body > *');
    expect(page?.hasAttribute('inert')).toBe(true);
    expect(viewer.closest('[inert]')).toBeNull();

    await user.click(within(viewer).getByRole('button', { name: ru.kyc.viewer.close }));
    expect(page?.hasAttribute('inert')).toBe(false);
  });

  it('asks for a new link when the photo is opened after the old one expired', async () => {
    const { kycReview } = openApplication(
      kycApplication({ documents: [{ kind: 'passport', uploaded_at: '2026-10-23T08:50:00Z' }] }),
    );
    await screen.findByRole('img', { name: kinds.passport });

    vi.useFakeTimers({ now: Date.now() + 300_000, toFake: ['Date'] });
    fireEvent.click(screen.getByRole('button', { name: `Открыть крупно: ${kinds.passport}` }));

    const viewer = await screen.findByRole('dialog', { name: kinds.passport });
    expect(within(viewer).getByRole('img').getAttribute('src')).toBe(
      'http://storage.test/passport.jpg?link=2',
    );
    expect(kycReview.documentUrl).toHaveBeenCalledTimes(2);

    fireEvent.click(within(viewer).getByRole('button', { name: ru.kyc.viewer.close }));
    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: `Открыть крупно: ${kinds.passport}` }),
    );
  });

  it('closes with Escape at the largest zoom, with focus kept on the zoom button', async () => {
    const user = userEvent.setup();
    openApplication();
    await user.click(
      await screen.findByRole('button', { name: `Открыть крупно: ${kinds.passport}` }),
    );
    const viewer = screen.getByRole('dialog', { name: kinds.passport });
    const zoomIn = within(viewer).getByRole('button', { name: ru.kyc.viewer.zoomIn });

    for (let step = 0; step < 7; step += 1) await user.click(zoomIn);
    expect(within(viewer).getByRole('status').textContent).toBe('400\u00a0%');
    expect(zoomIn.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(zoomIn);

    // Wherever focus is: the listener is on the document.
    document.body.focus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('asks for one fresh link however often an expired photo is clicked', async () => {
    const { kycReview } = openApplication(
      kycApplication({ documents: [{ kind: 'passport', uploaded_at: '2026-10-23T08:50:00Z' }] }),
    );
    await screen.findByRole('img', { name: kinds.passport });
    let answer: (link: { url: string; expires_in: number }) => void = () => undefined;
    kycReview.documentUrl.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );

    vi.useFakeTimers({ now: Date.now() + 300_000, toFake: ['Date'] });
    const open = screen.getByRole('button', { name: `Открыть крупно: ${kinds.passport}` });
    fireEvent.click(open);
    fireEvent.click(open);
    fireEvent.click(open);
    answer({ url: 'http://storage.test/passport.jpg?fresh', expires_in: 300 });

    expect(await screen.findByRole('dialog', { name: kinds.passport })).toBeDefined();
    expect(kycReview.documentUrl).toHaveBeenCalledTimes(2);
  });

  it('does not open the photo by itself later when a fresh link was refused', async () => {
    const { kycReview } = openApplication(
      kycApplication({ documents: [{ kind: 'passport', uploaded_at: '2026-10-23T08:50:00Z' }] }),
    );
    await screen.findByRole('img', { name: kinds.passport });
    kycReview.documentUrl.mockRejectedValueOnce(new ApiError(429, 'rate-limited'));

    vi.useFakeTimers({ now: Date.now() + 300_000, toFake: ['Date'] });
    fireEvent.click(screen.getByRole('button', { name: `Открыть крупно: ${kinds.passport}` }));
    fireEvent.click(await screen.findByRole('button', { name: ru.kyc.documents.reload }));

    expect(await screen.findByRole('img', { name: kinds.passport })).toBeDefined();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('approves with the license issue date and returns to the queue', async () => {
    const user = userEvent.setup();
    const { kycReview, application } = openApplication();
    await screen.findByRole('heading', { level: 1 });

    fireEvent.change(within(approveForm()).getByLabelText(ru.kyc.approve.licenseIssuedOn), {
      target: { value: '2015-06-01' },
    });
    await user.click(within(approveForm()).getByRole('button', { name: ru.kyc.approve.submit }));

    expect(await screen.findByText(ru.kyc.queue.approved)).toBeDefined();
    expect(kycReview.approve).toHaveBeenCalledWith(application.id, '2015-06-01');
  });

  it('does not approve without the license issue date', async () => {
    const user = userEvent.setup();
    const { kycReview } = openApplication();
    await screen.findByRole('heading', { level: 1 });

    await user.click(within(approveForm()).getByRole('button', { name: ru.kyc.approve.submit }));

    expect(within(approveForm()).getByRole('alert').textContent).toBe(ru.kyc.errors.dateRequired);
    expect(kycReview.approve).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError(409, 'kyc-requirements-not-met'), ru.kyc.errors.requirementsNotMet],
    [new ApiError(409, 'kyc-profile-incomplete'), ru.kyc.errors.profileIncomplete],
    [new ApiError(422, 'license-issue-date-invalid'), ru.kyc.errors.licenseDateInvalid],
    [new ApiError(422, 'validation-error'), ru.kyc.errors.invalid],
    [new ApiError(403, 'forbidden'), ru.kyc.errors.forbidden],
    [new NetworkError(new TypeError('offline')), ru.kyc.errors.failed],
  ])('explains a refused approval: %s', async (error, message) => {
    const user = userEvent.setup();
    const { kycReview } = openApplication();
    kycReview.approve.mockRejectedValueOnce(error);
    await screen.findByRole('heading', { level: 1 });

    fireEvent.change(within(approveForm()).getByLabelText(ru.kyc.approve.licenseIssuedOn), {
      target: { value: '2015-06-01' },
    });
    await user.click(within(approveForm()).getByRole('button', { name: ru.kyc.approve.submit }));

    expect((await within(approveForm()).findByRole('alert')).textContent).toBe(message);
    expect(
      within(approveForm()).getByRole('button', { name: ru.kyc.approve.submit }),
    ).toHaveProperty('disabled', false);
  });

  it('rejects with the trimmed reason and returns to the queue', async () => {
    const user = userEvent.setup();
    const { kycReview, application } = openApplication();
    await screen.findByRole('heading', { level: 1 });

    await user.type(
      within(rejectForm()).getByLabelText(ru.kyc.reject.reason),
      '  The selfie is blurry  ',
    );
    expect(rejectForm().textContent).toContain('20 из 500');
    await user.click(within(rejectForm()).getByRole('button', { name: ru.kyc.reject.submit }));

    expect(await screen.findByText(ru.kyc.queue.rejected)).toBeDefined();
    expect(kycReview.reject).toHaveBeenCalledWith(application.id, 'The selfie is blurry');
  });

  it.each([
    [new ApiError(422, 'validation-error'), ru.kyc.errors.invalid],
    [new ApiError(403, 'forbidden'), ru.kyc.errors.forbidden],
    [new NetworkError(new TypeError('offline')), ru.kyc.errors.failed],
  ])('explains a refused rejection: %s', async (error, message) => {
    const user = userEvent.setup();
    const { kycReview } = openApplication();
    kycReview.reject.mockRejectedValueOnce(error);
    await screen.findByRole('heading', { level: 1 });

    await user.type(within(rejectForm()).getByLabelText(ru.kyc.reject.reason), 'Blurry');
    await user.click(within(rejectForm()).getByRole('button', { name: ru.kyc.reject.submit }));

    expect((await within(rejectForm()).findByRole('alert')).textContent).toBe(message);
    expect(within(rejectForm()).getByRole('button', { name: ru.kyc.reject.submit })).toHaveProperty(
      'disabled',
      false,
    );
  });

  it('does not reject without a reason or with a too long one', async () => {
    const user = userEvent.setup();
    const { kycReview } = openApplication();
    await screen.findByRole('heading', { level: 1 });
    const reason = within(rejectForm()).getByLabelText(ru.kyc.reject.reason);
    const submit = within(rejectForm()).getByRole('button', { name: ru.kyc.reject.submit });

    await user.type(reason, '   ');
    await user.click(submit);
    expect(within(rejectForm()).getByRole('alert').textContent).toBe(ru.kyc.errors.reasonRequired);

    fireEvent.change(reason, { target: { value: 'x'.repeat(501) } });
    await user.click(submit);
    expect(within(rejectForm()).getByRole('alert').textContent).toBe(
      'Причина длиннее 500 символов.',
    );
    expect(kycReview.reject).not.toHaveBeenCalled();
  });

  it('shows the decision of another operator instead of the forms', async () => {
    const user = userEvent.setup();
    const pending = kycApplication();
    const { kycReview } = openApplication(pending);
    kycReview.reject.mockRejectedValueOnce(new ApiError(409, 'kyc-not-pending'));
    await screen.findByRole('heading', { level: 1 });
    kycReview.application.mockResolvedValue({
      ...pending,
      status: 'rejected',
      rejection_reason: 'Passport expired',
    });

    await user.type(within(rejectForm()).getByLabelText(ru.kyc.reject.reason), 'Blurry');
    await user.click(within(rejectForm()).getByRole('button', { name: ru.kyc.reject.submit }));

    expect(await screen.findByText(ru.kyc.errors.notPending)).toBeDefined();
    expect(await screen.findByText(ru.kyc.decided)).toBeDefined();
    // The photos stayed: no new links, so no views in the audit log the operator did not make.
    expect(kycReview.documentUrl).toHaveBeenCalledTimes(4);
    expect(screen.getByText('Причина отказа: Passport expired')).toBeDefined();
    expect(screen.queryByRole('form', { name: ru.kyc.approve.title })).toBeNull();
  });

  it('says when the application does not exist', async () => {
    const kycReview = fakeKycReviewApi();
    kycReview.application.mockRejectedValue(new ApiError(404, 'kyc-application-not-found'));
    renderAt('/kyc/0199b000-1111-7000-8000-00000000ffff', testStaff('operator'), { kycReview });

    expect(await screen.findByText(ru.kyc.notFound)).toBeDefined();
  });

  it('does not ask the API about a malformed id', async () => {
    const kycReview = fakeKycReviewApi();
    renderAt('/kyc/not-an-id', testStaff('operator'), { kycReview });

    expect(await screen.findByText(ru.kyc.notFound)).toBeDefined();
    expect(kycReview.application).not.toHaveBeenCalled();
  });

  it('says when the client has not submitted the application', async () => {
    openApplication(kycApplication({ status: 'not_submitted', submitted_at: null }));

    expect(await screen.findByText(ru.kyc.notSubmitted)).toBeDefined();
    expect(screen.queryByText(ru.kyc.decided)).toBeNull();
    expect(screen.queryByRole('form', { name: ru.kyc.approve.title })).toBeNull();
  });

  it('says when the role may not open applications', async () => {
    const kycReview = fakeKycReviewApi();
    kycReview.application.mockRejectedValue(new ApiError(403, 'forbidden'));
    renderAt('/kyc/0199b000-1111-7000-8000-00000000ffff', testStaff('operator'), { kycReview });

    expect(await screen.findByText(ru.forbidden.text)).toBeDefined();
  });

  it('starts clean when another application opens in the same page', async () => {
    const user = userEvent.setup();
    const first = kycApplication({ first_name: 'Ivan', last_name: 'Petrov' });
    const second = kycApplication({ first_name: 'Anna', last_name: 'Kovaleva' });
    const kycReview = fakeKycReviewApi({ application: first });
    kycReview.application.mockImplementation((id) =>
      Promise.resolve(id === second.id ? second : first),
    );
    const { router } = renderAt(`/kyc/${first.id}`, testStaff('operator'), { kycReview });
    await screen.findByRole('heading', { level: 1, name: 'Petrov Ivan' });
    await user.type(within(rejectForm()).getByLabelText(ru.kyc.reject.reason), 'Blurry');

    await router.navigate(`/kyc/${second.id}`);

    expect(await screen.findByRole('heading', { level: 1, name: 'Kovaleva Anna' })).toBeDefined();
    expect(within(rejectForm()).getByLabelText(ru.kyc.reject.reason)).toHaveProperty('value', '');
  });

  it('offers to retry when the application does not load', async () => {
    const application = kycApplication();
    const kycReview = fakeKycReviewApi({ application });
    kycReview.application.mockRejectedValueOnce(new NetworkError(new TypeError('offline')));
    renderAt(`/kyc/${application.id}`, testStaff('operator'), { kycReview });

    const alert = await screen.findByRole('alert');
    await userEvent.click(within(alert).getByRole('button', { name: ru.kyc.retry }));

    expect(await screen.findByRole('heading', { level: 1, name: 'Petrov Ivan' })).toBeDefined();
  });

  it('signs out when the session is over', async () => {
    const application = kycApplication();
    const kycReview = fakeKycReviewApi({ application });
    kycReview.application.mockRejectedValue(new ApiError(401, 'access-token-invalid'));
    const { api } = renderAt(`/kyc/${application.id}`, testStaff('operator'), { kycReview });

    await waitFor(() => {
      expect(api.logout).toHaveBeenCalled();
    });
  });
});
