import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import type { DocumentKind, KycApplication } from './kycApi';
import { KycProvider } from './KycProvider';
import { KycStore } from './kycStore';
import { KycUploadScreen } from './KycUploadScreen';
import { discardPhoto, pickPhoto } from './photos';
import { fakeKycApi, kycApplication, ok } from './testing';

initI18n('ru');

const mockRouter = { back: jest.fn(), dismissTo: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));
jest.mock('./photos', () => ({ pickPhoto: jest.fn(), discardPhoto: jest.fn() }));

const mockPick = jest.mocked(pickPhoto);
const mockDiscard = jest.mocked(discardPhoto);

const allDocuments: DocumentKind[] = [
  'license_front',
  'license_back',
  'passport',
  'selfie_with_license',
];

async function renderUpload(application: KycApplication) {
  const { api, uploads } = fakeKycApi();
  api.getApplication.mockResolvedValue(ok(application));
  const view = await render(
    <KycProvider store={new KycStore(api)}>
      <KycUploadScreen />
    </KycProvider>,
  );
  return { api, uploads, view };
}

function last<T>(items: readonly T[]): T {
  const item = items.at(-1);
  if (item === undefined) throw new Error('No items');
  return item;
}

async function choosePhoto(uri: string) {
  mockPick.mockResolvedValueOnce({ kind: 'picked', uri });
  await fireEvent.press(await screen.findByRole('button', { name: 'Сфотографировать' }));
  await fireEvent.press(await screen.findByRole('button', { name: 'Отправить фото' }));
}

describe('KycUploadScreen', () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it('walks through the documents one by one', async () => {
    const { api, uploads } = await renderUpload(kycApplication());

    expect(await screen.findByText('Шаг 1 из 4')).toBeOnTheScreen();
    expect(screen.getByText('Права: лицевая сторона')).toBeOnTheScreen();

    await choosePhoto('file:///cache/front.jpg');
    expect(api.upload).toHaveBeenCalledWith(
      'license_front',
      'file:///cache/front.jpg',
      expect.any(Function),
    );

    await act(() => {
      uploads[0]?.onProgress?.(0.5);
    });
    expect(screen.getByText('Отправляем фото: 50%')).toBeOnTheScreen();
    expect(screen.getByRole('progressbar').props.accessibilityValue).toMatchObject({ now: 50 });

    await act(() => {
      uploads[0]?.finish(ok(kycApplication(['license_front'])));
    });

    expect(await screen.findByText('Права: обратная сторона')).toBeOnTheScreen();
    expect(screen.getByText('Шаг 2 из 4')).toBeOnTheScreen();
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/front.jpg');
  });

  it('opens the front camera for the selfie', async () => {
    await renderUpload(kycApplication(['license_front', 'license_back', 'passport']));
    mockPick.mockResolvedValueOnce({ kind: 'canceled' });

    await fireEvent.press(await screen.findByRole('button', { name: 'Сфотографировать' }));

    expect(mockPick).toHaveBeenCalledWith('camera', { selfie: true });
  });

  it('sends the same photo again after a network error', async () => {
    const { api, uploads } = await renderUpload(kycApplication());

    await choosePhoto('file:///cache/front.jpg');
    await act(() => {
      uploads[0]?.finish({ ok: false, error: { kind: 'network' } });
    });
    expect(await screen.findByText('Нет связи с сервером. Проверьте интернет')).toBeOnTheScreen();
    expect(mockDiscard).not.toHaveBeenCalled();

    await fireEvent.press(screen.getByRole('button', { name: 'Повторить' }));

    expect(api.upload).toHaveBeenCalledTimes(2);
    expect(api.upload).toHaveBeenLastCalledWith(
      'license_front',
      'file:///cache/front.jpg',
      expect.any(Function),
    );
  });

  it('asks for another photo when the API cannot use this one', async () => {
    const { uploads } = await renderUpload(kycApplication());

    await choosePhoto('file:///cache/front.jpg');
    await act(() => {
      uploads[0]?.finish({ ok: false, error: { kind: 'unsupported' } });
    });

    expect(await screen.findByText(/Не удалось прочитать фото/)).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeOnTheScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Выбрать другое фото' }));

    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/front.jpg');
    expect(screen.getByRole('button', { name: 'Сфотографировать' })).toBeOnTheScreen();
  });

  it('explains a denied camera', async () => {
    await renderUpload(kycApplication());
    mockPick.mockResolvedValueOnce({ kind: 'cameraDenied' });

    await fireEvent.press(await screen.findByRole('button', { name: 'Сфотографировать' }));

    expect(await screen.findByText(/Нет доступа к камере/)).toBeOnTheScreen();
  });

  it('cancels the upload and deletes the photo when the client leaves', async () => {
    const { uploads, view } = await renderUpload(kycApplication());

    await choosePhoto('file:///cache/front.jpg');
    await view.unmount();

    expect(uploads[0]?.aborted).toBe(true);
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/front.jpg');
  });

  it('sends a complete application for review', async () => {
    const { api } = await renderUpload(kycApplication(allDocuments));
    api.submit.mockResolvedValue(ok(kycApplication(allDocuments, { status: 'pending' })));

    expect(await screen.findByText('Ваши документы')).toBeOnTheScreen();
    expect(screen.queryByText('Не загружено')).not.toBeOnTheScreen();

    await fireEvent.press(screen.getByRole('button', { name: 'Отправить на проверку' }));

    await waitFor(() => {
      expect(mockRouter.dismissTo).toHaveBeenCalledWith('/kyc');
    });
  });

  it('lets a rejected client replace a single photo', async () => {
    await renderUpload(
      kycApplication(allDocuments, { status: 'rejected', rejection_reason: 'Селфи размыто' }),
    );

    const replace = await screen.findAllByRole('button', { name: 'Заменить' });
    await fireEvent.press(last(replace));

    expect(screen.getByText('Селфи с правами')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'К списку документов' }));
    expect(screen.getByText('Ваши документы')).toBeOnTheScreen();
  });

  it('does not submit while documents are missing', async () => {
    await renderUpload(kycApplication(['license_front']));

    await fireEvent.press(await screen.findByRole('button', { name: 'К списку документов' }));

    expect(screen.getAllByText('Не загружено')).toHaveLength(3);
    expect(screen.getByRole('button', { name: 'Отправить на проверку' })).toBeDisabled();
  });

  it('leaves for the status once the application is under review', async () => {
    await renderUpload(kycApplication(allDocuments, { status: 'pending' }));

    await waitFor(() => {
      expect(mockRouter.dismissTo).toHaveBeenCalledWith('/kyc');
    });
  });
});
