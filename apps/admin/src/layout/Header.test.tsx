import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import en from '../i18n/en.json';
import ru from '../i18n/ru.json';
import { renderAt } from '../test/render';

describe('header', () => {
  it('switches the interface language', async () => {
    renderAt('/');

    await userEvent.selectOptions(screen.getByLabelText(ru.header.language), 'en');

    expect(screen.getByRole('link', { name: en.nav.fleet })).toBeDefined();
    expect(document.documentElement.lang).toBe('en');
  });

  it('switches between the light and the dark theme', async () => {
    renderAt('/');
    const toggle = screen.getByRole('button', { name: ru.header.darkTheme });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');

    await userEvent.click(toggle);

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');

    await userEvent.click(toggle);

    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
