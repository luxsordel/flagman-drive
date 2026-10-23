import { fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { KycProvider } from '../kyc/KycProvider';
import { KycStore } from '../kyc/kycStore';
import { fakeKycApi, kycApplication, ok } from '../kyc/testing';
import { HomeScreen } from './HomeScreen';

initI18n('ru');

const mockRouter = { push: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

async function renderHome() {
  const kyc = fakeKycApi();
  kyc.api.getApplication.mockResolvedValue(ok(kycApplication([], { status: 'rejected' })));
  await render(
    <KycProvider store={new KycStore(kyc.api)}>
      <HomeScreen />
    </KycProvider>,
  );
}

describe('HomeScreen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('opens the profile', async () => {
    await renderHome();

    await fireEvent.press(screen.getByRole('button', { name: 'Профиль' }));

    expect(mockRouter.push).toHaveBeenCalledWith('/profile');
  });

  it('shows the document status and opens it', async () => {
    await renderHome();

    expect(await screen.findByText('Отклонены')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Открыть' }));

    expect(mockRouter.push).toHaveBeenCalledWith('/kyc');
  });
});
