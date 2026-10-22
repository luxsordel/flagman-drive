import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import ru from '../i18n/ru.json';
import { renderAt } from '../test/render';

describe('staff sign-in stub', () => {
  it('asks for email, password and the 2FA code', () => {
    renderAt('/login');

    expect(screen.getByLabelText(ru.login.email).getAttribute('type')).toBe('email');
    expect(screen.getByLabelText(ru.login.password).getAttribute('type')).toBe('password');
    expect(screen.getByLabelText(ru.login.totp).getAttribute('autocomplete')).toBe('one-time-code');
  });

  it('does not send anything to the network yet', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    renderAt('/login');

    await userEvent.type(screen.getByLabelText(ru.login.email), 'operator@example.com');
    await userEvent.type(screen.getByLabelText(ru.login.password), 'not-a-real-password');
    await userEvent.type(screen.getByLabelText(ru.login.totp), '123456');
    await userEvent.click(screen.getByRole('button', { name: ru.login.submit }));

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toBe(ru.login.notConnected);
  });
});
