import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { AuthProvider } from '../auth/session/AuthProvider';
import { testSession } from '../auth/session/testing';
import { HomeScreen } from './HomeScreen';

initI18n('ru');

describe('HomeScreen', () => {
  it('signs out', async () => {
    const { api, session } = testSession({ refreshToken: 'refresh-0' });
    api.on('/auth/token/refresh', new Response('{}', { status: 500 }));
    api.on('/auth/logout', new Response(null, { status: 204 }));
    await session.restore();
    await render(
      <AuthProvider session={session}>
        <HomeScreen />
      </AuthProvider>,
    );

    await fireEvent.press(screen.getByRole('button', { name: 'Выйти' }));

    await waitFor(() => {
      expect(session.getStatus()).toBe('signedOut');
    });
    expect(api.calls.at(-1)).toEqual({
      path: '/auth/logout',
      body: { refresh_token: 'refresh-0' },
    });
  });
});
