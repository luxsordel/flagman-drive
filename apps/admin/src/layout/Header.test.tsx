import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import en from '../i18n/en.json';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';

describe('header', () => {
  it('switches the interface language', async () => {
    renderAt('/');

    await userEvent.selectOptions(await screen.findByLabelText(ru.header.language), 'en');

    expect(screen.getByRole('link', { name: en.nav.fleet })).toBeDefined();
    expect(document.documentElement.lang).toBe('en');
  });

  it('switches between the light and the dark theme', async () => {
    renderAt('/');
    const toggle = await screen.findByRole('button', { name: ru.header.darkTheme });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');

    await userEvent.click(toggle);

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');

    await userEvent.click(toggle);

    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('shows who is signed in and their role', async () => {
    renderAt('/', testStaff('operator'));

    expect(await screen.findByText('operator@example.com')).toBeDefined();
    expect(screen.getByText(ru.roles.operator)).toBeDefined();
  });

  it('signs out and returns to the sign-in page', async () => {
    const { api } = renderAt('/');

    await userEvent.click(await screen.findByRole('button', { name: ru.header.signOut }));

    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(api.logout).toHaveBeenCalledOnce();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('warns when the server did not end the session and lets the staff member retry', async () => {
    const { api } = renderAt('/');
    api.logout.mockRejectedValueOnce(new Error('network'));

    await userEvent.click(await screen.findByRole('button', { name: ru.header.signOut }));

    expect((await screen.findByRole('alert')).textContent).toContain(ru.login.logoutFailed);

    await userEvent.click(screen.getByRole('button', { name: ru.login.retryLogout }));

    expect(api.logout).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
