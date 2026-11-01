import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { initI18n } from '../../i18n';
import { ru } from '../../i18n/locales/ru';
import { ChatStore } from './chatStore';
import { IncidentScreen } from './IncidentScreen';
import { SupportProvider } from './SupportProvider';
import { SupportScreen } from './SupportScreen';
import { fakeSupportApi, message, offline, page } from './testing';

initI18n('ru');

const mockRouter = { push: jest.fn(), back: jest.fn(), replace: jest.fn() };
let mockParams: { rentalId?: string } = {};
jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => mockParams,
}));

const mockPick = jest.fn();
const mockDiscard = jest.fn();
jest.mock('../kyc/photos', () => ({
  pickPhoto: (...args: unknown[]) => mockPick(...args) as unknown,
  discardPhoto: (uri: string) => {
    mockDiscard(uri);
  },
}));

const mockLocate = jest.fn();
jest.mock('../map/location', () => ({ locateOnce: () => mockLocate() as unknown }));

async function renderWith(ui: React.ReactElement, api = fakeSupportApi()) {
  const chat = new ChatStore(api, () => 'id-1');
  await render(<SupportProvider deps={{ api, chat }}>{ui}</SupportProvider>);
  return api;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
  mockLocate.mockResolvedValue({ kind: 'located', location: { lat: 53.9, lon: 27.56 } });
});

describe('SupportScreen', () => {
  it('shows the operator by name as FLAGMAN support', async () => {
    const api = fakeSupportApi();
    api.messages.mockResolvedValueOnce(page([message({ text: 'Good morning' })]));
    await renderWith(<SupportScreen />, api);

    expect(await screen.findByText('Good morning')).toBeTruthy();
    expect(screen.getByText('Поддержка FLAGMAN · Anna')).toBeTruthy();
  });

  it('attaches the rental the client writes from', async () => {
    mockParams = { rentalId: 'rental-1' };
    const api = await renderWith(<SupportScreen />);
    await screen.findByText(ru.support.empty);

    await fireEvent.changeText(screen.getByLabelText(ru.support.messageLabel), 'Door is stuck');
    await fireEvent.press(screen.getByRole('button', { name: ru.support.send }));

    await waitFor(() => {
      expect(api.sendText).toHaveBeenCalledWith('Door is stuck', 'rental-1', 'id-1');
    });
  });

  it('shows an unsent message with its state', async () => {
    const api = fakeSupportApi();
    api.sendText.mockResolvedValue(offline);
    await renderWith(<SupportScreen />, api);
    await screen.findByText(ru.support.empty);

    await fireEvent.changeText(screen.getByLabelText(ru.support.messageLabel), 'Hello?');
    await fireEvent.press(screen.getByRole('button', { name: ru.support.send }));

    expect(await screen.findByText(ru.support.outgoing.waiting)).toBeTruthy();
    expect(screen.getByText('Hello?')).toBeTruthy();
    expect(screen.getByText(ru.support.errors.network)).toBeTruthy();
  });

  it('sends a photo from the camera', async () => {
    mockPick.mockResolvedValue({ kind: 'picked', uri: 'file:///cache/a.jpg' });
    const api = await renderWith(<SupportScreen />);
    await screen.findByText(ru.support.empty);

    await act(async () => {
      await fireEvent.press(screen.getByRole('button', { name: ru.support.camera }));
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(api.sendPhoto).toHaveBeenCalledWith('file:///cache/a.jpg', 'id-1');
    });
    expect(mockPick).toHaveBeenCalledWith('camera');
  });

  it('explains a camera without permission', async () => {
    mockPick.mockResolvedValue({ kind: 'cameraDenied' });
    await renderWith(<SupportScreen />);
    await screen.findByText(ru.support.empty);

    await fireEvent.press(screen.getByRole('button', { name: ru.support.camera }));

    expect(await screen.findByText(ru.support.cameraDenied)).toBeTruthy();
  });

  it('asks for a photo link only on a tap', async () => {
    const api = fakeSupportApi();
    const photo = message({ kind: 'photo', text: null });
    api.messages.mockResolvedValueOnce(page([photo]));
    await renderWith(<SupportScreen />, api);

    const show = await screen.findByRole('button', { name: ru.support.showPhoto });
    expect(api.photoUrl).not.toHaveBeenCalled();

    await fireEvent.press(show);

    await waitFor(() => {
      expect(api.photoUrl).toHaveBeenCalledWith(photo.id);
    });
  });
});

describe('IncidentScreen', () => {
  const describeIt = async () => {
    await fireEvent.changeText(
      screen.getByLabelText(ru.support.incident.description),
      'Scratch on the door',
    );
  };

  it('reports with the place and the rental, then sends the photos', async () => {
    mockParams = { rentalId: 'rental-1' };
    mockPick.mockResolvedValue({ kind: 'picked', uri: 'file:///cache/a.jpg' });
    const api = await renderWith(<IncidentScreen />);

    await describeIt();
    await act(async () => {
      await fireEvent.press(screen.getByRole('button', { name: ru.support.library }));
      await Promise.resolve();
    });
    await screen.findByText('Фото: 1');
    await fireEvent.press(screen.getByRole('button', { name: ru.support.incident.submit }));

    expect(await screen.findByText(ru.support.incident.sentTitle)).toBeTruthy();
    expect(api.reportIncident).toHaveBeenCalledWith({
      type: 'damage',
      description: 'Scratch on the door',
      location: { lat: 53.9, lon: 27.56 },
      rentalId: 'rental-1',
    });
    expect(api.addIncidentPhoto).toHaveBeenCalledWith('incident-1', 'file:///cache/a.jpg');
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/a.jpg');
  });

  it('reports without a place when the client gave no permission', async () => {
    mockLocate.mockResolvedValue({ kind: 'denied' });
    const api = await renderWith(<IncidentScreen />);

    await describeIt();
    await fireEvent.press(screen.getByRole('button', { name: ru.support.incident.submit }));

    await screen.findByText(ru.support.incident.sentTitle);
    expect(api.reportIncident).toHaveBeenCalledWith(expect.objectContaining({ location: null }));
  });

  it('does not report twice when a photo failed', async () => {
    mockPick.mockResolvedValue({ kind: 'picked', uri: 'file:///cache/a.jpg' });
    const api = fakeSupportApi();
    api.addIncidentPhoto.mockResolvedValueOnce(offline);
    await renderWith(<IncidentScreen />, api);

    await describeIt();
    await act(async () => {
      await fireEvent.press(screen.getByRole('button', { name: ru.support.library }));
      await Promise.resolve();
    });
    await screen.findByText('Фото: 1');
    await fireEvent.press(screen.getByRole('button', { name: ru.support.incident.submit }));

    await fireEvent.press(
      await screen.findByRole('button', { name: ru.support.incident.sendPhotos }),
    );

    expect(await screen.findByText(ru.support.incident.sentTitle)).toBeTruthy();
    expect(api.reportIncident).toHaveBeenCalledTimes(1);
    expect(api.addIncidentPhoto).toHaveBeenCalledTimes(2);
  });

  it('needs a description', async () => {
    await renderWith(<IncidentScreen />);

    const submit = screen.getByRole('button', { name: ru.support.incident.submit });
    expect(submit.props.accessibilityState).toMatchObject({ disabled: true });
  });
});
