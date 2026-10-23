import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { InitialEntry } from 'react-router';
import { describe, expect, it } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import { fakeStaffSignInApi, testStaff } from '../test/auth';
import { renderAt } from '../test/render';

/** The sign-in page without a session; `me` answers once the sign-in hands over its token. */
async function openSignIn(path: InitialEntry = '/login', staffSignIn = fakeStaffSignInApi()) {
  const user = userEvent.setup();
  const rendered = renderAt(path, null, { staffSignIn });
  rendered.api.me.mockResolvedValue(testStaff('operator'));
  await screen.findByRole('heading', { name: ru.login.title });
  return { ...rendered, user, staffSignIn };
}

async function enterPassword(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText(ru.login.email), ' operator@example.com ');
  await user.type(screen.getByLabelText(ru.login.password), 'correct horse battery');
  await user.click(screen.getByRole('button', { name: ru.login.next }));
}

async function enterCode(user: ReturnType<typeof userEvent.setup>, code = '123456') {
  await user.type(await screen.findByLabelText(ru.login.totp), code);
  await user.click(screen.getByRole('button', { name: ru.login.submit }));
}

describe('staff sign-in', () => {
  it('signs in with the password and the 2FA code, then opens the page asked for', async () => {
    const { user, staffSignIn, api } = await openSignIn('/fleet');

    await enterPassword(user);
    await enterCode(user);

    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.fleet })).toBeDefined();
    expect(staffSignIn.checkPassword).toHaveBeenCalledWith(
      'operator@example.com',
      'correct horse battery',
    );
    expect(staffSignIn.startTotpEnrollment).not.toHaveBeenCalled();
    expect(staffSignIn.verifyTotp).toHaveBeenCalledWith('challenge-1', '123456');
    expect(api.me).toHaveBeenCalledWith('access-1');
  });

  it('opens the dashboard when no page was asked for', async () => {
    const { user } = await openSignIn();

    await enterPassword(user);
    await enterCode(user);

    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.dashboard })).toBeDefined();
  });

  it('shows the QR code and the secret before the first code of a staff member without 2FA', async () => {
    const { user, staffSignIn } = await openSignIn(
      '/login',
      fakeStaffSignInApi({ enrolled: false }),
    );

    await enterPassword(user);

    expect(await screen.findByRole('heading', { name: ru.login.enrollTitle })).toBeDefined();
    expect(staffSignIn.startTotpEnrollment).toHaveBeenCalledWith('challenge-1');
    expect(screen.getByText('JBSW Y3DP EHPK 3PXP')).toBeDefined();
    const qr = await screen.findByRole('img', { name: ru.login.qrAlt });
    expect(qr.getAttribute('src')).toMatch(/^data:image\/svg\+xml/);

    await enterCode(user);
    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.dashboard })).toBeDefined();
  });

  it('asks for the email and password before calling the API', async () => {
    const { user, staffSignIn } = await openSignIn();

    await user.click(screen.getByRole('button', { name: ru.login.next }));

    expect(screen.getByRole('alert').textContent).toBe(ru.login.errors.missingCredentials);
    expect(staffSignIn.checkPassword).not.toHaveBeenCalled();
  });

  it('says the email or password is wrong and stays on the first step', async () => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.checkPassword.mockRejectedValue(new ApiError(401, 'staff-credentials-invalid'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.credentials);
    expect(screen.getByLabelText(ru.login.password)).toBeDefined();
  });

  it('refuses a password longer than any account can have instead of cutting it', async () => {
    const { user, staffSignIn } = await openSignIn();

    await user.type(screen.getByLabelText(ru.login.email), 'operator@example.com');
    await user.click(screen.getByLabelText(ru.login.password));
    await user.paste('x'.repeat(129));
    expect(screen.getByLabelText<HTMLInputElement>(ru.login.password).value).toHaveLength(129);
    await user.click(screen.getByRole('button', { name: ru.login.next }));

    expect(screen.getByRole('alert').textContent).toBe(ru.login.errors.credentials);
    expect(staffSignIn.checkPassword).not.toHaveBeenCalled();
  });

  it('drops the password from the form once it is checked', async () => {
    const { user } = await openSignIn();

    await enterPassword(user);
    await screen.findByLabelText(ru.login.totp);
    await user.click(screen.getByRole('button', { name: ru.login.startOver }));

    expect(screen.getByLabelText<HTMLInputElement>(ru.login.password).value).toBe('');
    expect(screen.getByLabelText<HTMLInputElement>(ru.login.email).value).toBe(
      'operator@example.com',
    );
  });

  it('checks the code format before calling the API', async () => {
    const { user, staffSignIn } = await openSignIn();

    await enterPassword(user);
    await enterCode(user, '12a3');

    expect(screen.getByRole('alert').textContent).toBe(ru.login.errors.codeFormat);
    expect(staffSignIn.verifyTotp).not.toHaveBeenCalled();
  });

  it('lets the staff member retry a wrong code without the password', async () => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.verifyTotp.mockRejectedValueOnce(new ApiError(400, 'totp-invalid'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);
    await enterCode(user, '000000');

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.totpInvalid);
    expect(screen.getByLabelText<HTMLInputElement>(ru.login.totp).value).toBe('');

    await enterCode(user);
    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.dashboard })).toBeDefined();
    expect(staffSignIn.checkPassword).toHaveBeenCalledOnce();
  });

  it('goes back to the password when the challenge has expired', async () => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.verifyTotp.mockRejectedValue(new ApiError(401, 'staff-challenge-invalid'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);
    await enterCode(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.challengeExpired);
    expect(screen.getByLabelText(ru.login.password)).toBeDefined();
    expect(screen.queryByLabelText(ru.login.totp)).toBeNull();
  });

  it('sends the staff member to an administrator when 2FA must be reset', async () => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.verifyTotp.mockRejectedValue(new ApiError(409, 'totp-reset-required'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);
    await enterCode(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.resetRequired);
    expect(screen.getByLabelText(ru.login.password)).toBeDefined();
  });

  it('says how long to wait after too many attempts', async () => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.checkPassword.mockRejectedValue(new ApiError(429, 'rate-limited', 90));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);

    expect((await screen.findByRole('alert')).textContent).toBe(
      ru.login.errors.rateLimitedFor.replace('{{minutes}}', '2'),
    );
  });

  it.each([
    ['the network', new TypeError('Failed to fetch')],
    ['a proxy in front of a stopped API', new ApiError(502)],
  ])('tells an unreachable server apart from a refusal: %s', async (_, failure) => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.checkPassword.mockRejectedValue(failure);
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.network);
  });

  it('asks to reload when the profile does not load after a passed sign-in', async () => {
    const { user, api } = await openSignIn();
    api.me.mockRejectedValue(new TypeError('Failed to fetch'));

    await enterPassword(user);
    await enterCode(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.profileFailed);
    // The challenge is spent: sending the code again could only fail.
    expect(screen.queryByLabelText(ru.login.totp)).toBeNull();
    expect(screen.queryByRole('button', { name: ru.login.submit })).toBeNull();
    expect(screen.getByRole('button', { name: ru.login.reload })).toBeDefined();
  });

  it('keeps the challenge when getting the QR code fails, and retries with it', async () => {
    const staffSignIn = fakeStaffSignInApi({ enrolled: false });
    staffSignIn.startTotpEnrollment.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.network);
    expect(screen.queryByLabelText(ru.login.password)).toBeNull();

    await user.click(screen.getByRole('button', { name: ru.login.retry }));

    expect(await screen.findByRole('img', { name: ru.login.qrAlt })).toBeDefined();
    expect(staffSignIn.checkPassword).toHaveBeenCalledOnce();
    expect(staffSignIn.startTotpEnrollment).toHaveBeenNthCalledWith(2, 'challenge-1');
  });

  it('goes back to the password when 2FA turns out to be set up already', async () => {
    const staffSignIn = fakeStaffSignInApi({ enrolled: false });
    staffSignIn.startTotpEnrollment.mockRejectedValue(new ApiError(409, 'totp-already-enrolled'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.alreadyEnrolled);
    expect(screen.getByLabelText(ru.login.password)).toBeDefined();
  });

  it('does not start over while the code is being checked', async () => {
    const staffSignIn = fakeStaffSignInApi();
    let reject: (error: unknown) => void = () => undefined;
    staffSignIn.verifyTotp.mockReturnValueOnce(
      new Promise((_, fail) => {
        reject = fail;
      }),
    );
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);
    await enterCode(user);

    const startOver = screen.getByRole('button', { name: ru.login.startOver });
    expect(startOver.hasAttribute('disabled')).toBe(true);
    await user.click(startOver);
    reject(new ApiError(400, 'totp-invalid'));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.totpInvalid);
    expect(screen.getByLabelText(ru.login.totp)).toBeDefined();
  });

  it('sends nothing twice while a step is in flight', async () => {
    const staffSignIn = fakeStaffSignInApi();
    let finish: () => void = () => undefined;
    staffSignIn.checkPassword.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = () => {
          resolve({ challenge_token: 'challenge-1', expires_in: 300, next_step: 'totp' });
        };
      }),
    );
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);
    const next = screen.getByRole('button', { name: ru.login.next });
    expect(next.hasAttribute('disabled')).toBe(true);
    await user.click(next);
    finish();

    await screen.findByLabelText(ru.login.totp);
    expect(staffSignIn.checkPassword).toHaveBeenCalledOnce();
  });

  it('leaves the sign-in page for a staff member who already has a session', async () => {
    renderAt('/login', testStaff());

    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.dashboard })).toBeDefined();
  });

  it('ignores a redirect target outside the panel', async () => {
    const { user } = await openSignIn({ pathname: '/login', state: { from: '//evil.example' } });

    await enterPassword(user);
    await enterCode(user);

    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.dashboard })).toBeDefined();
  });

  it('checks the email format before calling the API', async () => {
    const { user, staffSignIn } = await openSignIn();

    await user.type(screen.getByLabelText(ru.login.email), 'оператор@флагман.бел');
    await user.type(screen.getByLabelText(ru.login.password), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: ru.login.next }));

    expect(screen.getByRole('alert').textContent).toBe(ru.login.errors.emailFormat);
    expect(staffSignIn.checkPassword).not.toHaveBeenCalled();
  });

  it('keeps the digits of a pasted code shown with a space', async () => {
    const { user, staffSignIn } = await openSignIn();

    await enterPassword(user);
    await user.click(await screen.findByLabelText(ru.login.totp));
    await user.paste('123 456\n');
    await user.click(screen.getByRole('button', { name: ru.login.submit }));

    expect(staffSignIn.verifyTotp).toHaveBeenCalledWith('challenge-1', '123456');
  });

  it('asks to reload when the answer to the code was lost on the network', async () => {
    const staffSignIn = fakeStaffSignInApi();
    staffSignIn.verifyTotp.mockRejectedValue(new TypeError('Failed to fetch'));
    const { user } = await openSignIn('/login', staffSignIn);

    await enterPassword(user);
    await enterCode(user);

    expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors.codeOutcomeUnknown);
    expect(screen.queryByLabelText(ru.login.totp)).toBeNull();
    expect(screen.getByRole('button', { name: ru.login.reload })).toBeDefined();
  });

  it.each([
    [
      'too many attempts without a wait time',
      new ApiError(429, 'rate-limited'),
      'rateLimited',
      true,
    ],
    [
      'a 2FA setup that was not started',
      new ApiError(409, 'totp-enrollment-not-started'),
      'challengeExpired',
      false,
    ],
    ['an API error without a known type', new ApiError(500), 'unexpected', true],
    ['a bug in the panel', new Error('bug'), 'unexpected', true],
  ] as const)(
    'explains %s and keeps the code step only when the challenge still works',
    async (_, error, key, keepsCode) => {
      const staffSignIn = fakeStaffSignInApi();
      staffSignIn.verifyTotp.mockRejectedValue(error);
      const { user } = await openSignIn('/login', staffSignIn);

      await enterPassword(user);
      await enterCode(user);

      expect((await screen.findByRole('alert')).textContent).toBe(ru.login.errors[key]);
      expect(screen.queryByLabelText(ru.login.totp) !== null).toBe(keepsCode);
    },
  );
});
