import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import ru from './i18n/ru.json';
import { sections, sectionsFor } from './navigation';
import { testStaff } from './test/auth';
import { renderAt } from './test/render';

describe('routes', () => {
  it('shows the staff sign-in page at /login', async () => {
    renderAt('/login', null);

    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('does not return to the page the staff member left by signing out', async () => {
    const user = userEvent.setup();
    const { api } = renderAt('/fleet', testStaff());
    await user.click(await screen.findByRole('button', { name: ru.header.signOut }));
    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(api.logout).toHaveBeenCalledOnce();

    await user.type(screen.getByLabelText(ru.login.email), 'operator@example.com');
    await user.type(screen.getByLabelText(ru.login.password), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: ru.login.next }));
    await user.type(await screen.findByLabelText(ru.login.totp), '123456');
    await user.click(screen.getByRole('button', { name: ru.login.submit }));

    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.dashboard })).toBeDefined();
  });

  it('sends a visitor without a session to the sign-in page', async () => {
    renderAt('/fleet', null);

    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
  });

  it('shows the layout with every section to an admin', async () => {
    renderAt('/');

    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    for (const section of sections) {
      expect(within(menu).getByRole('link', { name: ru.nav[section.key] })).toBeDefined();
    }
    expect(screen.getByRole('banner')).toBeDefined();
  });

  it('opens a section page from its path', async () => {
    renderAt('/fleet');

    expect(await screen.findByRole('heading', { level: 1, name: ru.nav.fleet })).toBeDefined();
    expect(screen.getByRole('link', { name: ru.nav.fleet }).getAttribute('aria-current')).toBe(
      'page',
    );
  });

  it('shows the not-found page for an unknown path', async () => {
    renderAt('/no-such-page');

    expect(await screen.findByRole('heading', { name: ru.notFound.title })).toBeDefined();
  });
});

describe('sections by role', () => {
  it('gives each role its own menu', () => {
    const keys = (role: Parameters<typeof sectionsFor>[0]) =>
      sectionsFor(role).map((section) => section.key);

    expect(keys('technician')).toEqual(['dashboard', 'fleet', 'zones']);
    expect(keys('operator')).toEqual([
      'dashboard',
      'kyc',
      'fleet',
      'rentals',
      'clients',
      'zones',
      'tariffs',
    ]);
    expect(keys('admin')).toEqual(sections.map((section) => section.key));
  });

  it('shows only the sections of the role in the menu', async () => {
    renderAt('/', testStaff('technician'));

    const menu = await screen.findByRole('navigation', { name: ru.layout.menu });
    expect(within(menu).getByRole('link', { name: ru.nav.fleet })).toBeDefined();
    expect(within(menu).queryByRole('link', { name: ru.nav.kyc })).toBeNull();
  });

  it('closes a section of another role opened by its address', async () => {
    renderAt('/audit', testStaff('operator'));

    expect(await screen.findByRole('heading', { name: ru.forbidden.title })).toBeDefined();
    expect(screen.queryByRole('heading', { name: ru.nav.audit })).toBeNull();
  });
});
