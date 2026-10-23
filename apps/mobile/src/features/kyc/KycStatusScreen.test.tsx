import { fireEvent, render, screen } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { KycProvider } from './KycProvider';
import { KycStatusScreen } from './KycStatusScreen';
import { KycStore } from './kycStore';
import { fakeKycApi, kycApplication, ok } from './testing';
import type { KycResult } from './kycApi';

initI18n('ru');

const mockRouter = { push: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

const allDocuments = ['license_front', 'license_back', 'passport', 'selfie_with_license'] as const;

async function renderStatus(...answers: KycResult[]) {
  const { api } = fakeKycApi();
  for (const answer of answers) api.getApplication.mockResolvedValueOnce(answer);
  await render(
    <KycProvider store={new KycStore(api)}>
      <KycStatusScreen />
    </KycProvider>,
  );
  return api;
}

describe('KycStatusScreen', () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it('asks to upload documents that were never sent', async () => {
    await renderStatus(ok(kycApplication()));

    expect(await screen.findByText('Не отправлены')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Загрузить документы' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/kyc/upload');
  });

  it('shows an application under review with the date it was sent', async () => {
    const api = await renderStatus(
      ok(
        kycApplication([...allDocuments], {
          status: 'pending',
          submitted_at: '2026-10-23T09:30:00Z',
        }),
      ),
      ok(kycApplication([...allDocuments], { status: 'approved' })),
    );

    expect(await screen.findByText('На проверке')).toBeOnTheScreen();
    expect(screen.getByText(/^Отправлены /)).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Загрузить документы' })).not.toBeOnTheScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Обновить статус' }));

    expect(await screen.findByText('Одобрены')).toBeOnTheScreen();
    expect(api.getApplication).toHaveBeenCalledTimes(2);
  });

  it('confirms approved documents without offering changes', async () => {
    await renderStatus(ok(kycApplication([...allDocuments], { status: 'approved' })));

    expect(
      await screen.findByText('Документы одобрены, можно арендовать машину'),
    ).toBeOnTheScreen();
    expect(screen.getAllByRole('button')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'На главную' })).toBeOnTheScreen();
  });

  it('shows the rejection reason and offers to send again', async () => {
    await renderStatus(
      ok(
        kycApplication([...allDocuments], {
          status: 'rejected',
          rejection_reason: 'Селфи размыто',
        }),
      ),
    );

    expect(await screen.findByText('Причина: Селфи размыто')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Исправить и отправить заново' }));
    expect(mockRouter.push).toHaveBeenCalledWith('/kyc/upload');
  });

  it('offers a retry when the status cannot be loaded', async () => {
    await renderStatus({ ok: false, error: { kind: 'network' } }, ok(kycApplication()));

    expect(await screen.findByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Обновить статус' }));
    expect(await screen.findByText('Не отправлены')).toBeOnTheScreen();
  });
});
