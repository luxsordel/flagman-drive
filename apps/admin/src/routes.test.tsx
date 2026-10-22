import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ru from './i18n/ru.json';
import { sections } from './navigation';
import { renderAt } from './test/render';

describe('routes', () => {
  it('shows the staff sign-in page at /login', () => {
    renderAt('/login');

    expect(screen.getByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('shows the layout with every section in the side menu', () => {
    renderAt('/');

    const menu = screen.getByRole('navigation', { name: ru.layout.menu });
    for (const section of sections) {
      expect(within(menu).getByRole('link', { name: ru.nav[section.key] })).toBeDefined();
    }
    expect(screen.getByRole('banner')).toBeDefined();
  });

  it('opens a section page from its path', () => {
    renderAt('/fleet');

    expect(screen.getByRole('heading', { level: 1, name: ru.nav.fleet })).toBeDefined();
    expect(screen.getByRole('link', { name: ru.nav.fleet }).getAttribute('aria-current')).toBe(
      'page',
    );
  });

  it('shows the not-found page for an unknown path', () => {
    renderAt('/no-such-page');

    expect(screen.getByRole('heading', { name: ru.notFound.title })).toBeDefined();
  });
});
