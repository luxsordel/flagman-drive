import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { AuthProvider } from '../auth/session/AuthProvider';
import { testSession } from '../auth/session/testing';
import { KycProvider } from '../kyc/KycProvider';
import { KycStore } from '../kyc/kycStore';
import { fakeKycApi, kycApplication, ok } from '../kyc/testing';
import { HomeScreen } from './HomeScreen';

initI18n('ru');

const mockRouter = { push: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

async function renderHome() {
  const setup = testSession({ refreshToken: 'refresh-0' });
  const kyc = fakeKycApi();
  kyc.api.getApplication.mockResolvedValue(ok(kycApplication([], { status: 'rejected' })));
  await render(
    <AuthProvider session={setup.session}>
      <KycProvider store={new KycStore(kyc.api)}>
        <HomeScreen />
      </KycProvider>
    </AuthProvider>,
  );
  return setup;
}

describe('HomeScreen', () => {
  it('signs out', async () => {
    const { api, session } = await renderHome();
    api.on('/auth/token/refresh', new Response('{}', { status: 500 }));
    api.on('/auth/logout', new Response(null, { status: 204 }));
    await session.restore();

    await fireEvent.press(screen.getByRole('button', { name: 'Выйти' }));

    await waitFor(() => {
      expect(session.getStatus()).toBe('signedOut');
    });
    expect(api.calls.at(-1)).toEqual({
      path: '/auth/logout',
      body: { refresh_token: 'refresh-0' },
    });
  });

  it('shows the document status and opens it', async () => {
    await renderHome();

    expect(await screen.findByText('Отклонены')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Открыть' }));

    expect(mockRouter.push).toHaveBeenCalledWith('/kyc');
  });
});
