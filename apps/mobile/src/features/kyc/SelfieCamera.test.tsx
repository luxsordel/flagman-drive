import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { BackHandler } from 'react-native';

import { initI18n } from '../../i18n';
import { discardPhoto, replaceWithJpeg } from './photos';
import { allowSelfieCamera, SelfieCamera } from './SelfieCamera';

initI18n('ru');

interface CameraProps {
  facing?: string;
  mirror?: boolean;
  onCameraReady?: () => void;
  onMountError?: () => void;
}

const mockCamera: { props: CameraProps } = { props: {} };
const mockTake = jest.fn<Promise<unknown>, [unknown]>();
const mockRequestPermission = jest.fn<Promise<{ granted: boolean }>, []>();

// The native camera does not exist under Jest: a stand-in that records its props.
jest.mock('expo-camera', () => {
  const { Component, createElement } = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  class CameraView extends Component<CameraProps> {
    takePictureAsync = mockTake;
    override render() {
      mockCamera.props = this.props;
      return createElement(View);
    }
  }
  return {
    CameraView,
    Camera: { requestCameraPermissionsAsync: () => mockRequestPermission() },
  };
});
jest.mock('./photos', () => ({ replaceWithJpeg: jest.fn(), discardPhoto: jest.fn() }));

const mockReplace = jest.mocked(replaceWithJpeg);
const mockDiscard = jest.mocked(discardPhoto);
const shot = { uri: 'file:///cache/Camera/shot.jpg', width: 3000, height: 4000 };

async function renderCamera() {
  const handlers = { onTaken: jest.fn(), onCancel: jest.fn(), onError: jest.fn() };
  const view = await render(<SelfieCamera {...handlers} />);
  return { ...handlers, view };
}

async function cameraReady() {
  await act(() => {
    mockCamera.props.onCameraReady?.();
  });
}

describe('SelfieCamera', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockTake.mockResolvedValue(shot);
    mockReplace.mockResolvedValue('file:///cache/out.jpg');
  });

  it('opens the front camera without mirroring the shot', async () => {
    await renderCamera();

    expect(mockCamera.props).toMatchObject({ facing: 'front', mirror: false });
    expect(screen.getByRole('button', { name: 'Снять' })).toBeDisabled();
  });

  it('hands back the re-encoded JPEG copy of the shot', async () => {
    const { onTaken } = await renderCamera();
    await cameraReady();

    await fireEvent.press(screen.getByRole('button', { name: 'Снять' }));

    expect(mockTake).toHaveBeenCalledWith({ exif: false, quality: 1 });
    expect(mockReplace).toHaveBeenCalledWith(shot);
    expect(onTaken).toHaveBeenCalledWith('file:///cache/out.jpg');
  });

  it('takes one photo on a double tap', async () => {
    let finish: (value: unknown) => void = () => undefined;
    mockTake.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { onTaken } = await renderCamera();
    await cameraReady();
    const shoot = screen.getByRole('button', { name: 'Снять' });

    // Both presses land before the button re-renders as disabled.
    await act(() => {
      void fireEvent.press(shoot);
      void fireEvent.press(shoot);
    });
    await act(async () => {
      finish(shot);
      await Promise.resolve();
    });

    expect(mockTake).toHaveBeenCalledTimes(1);
    expect(onTaken).toHaveBeenCalledTimes(1);
  });

  it('reports a failed shot', async () => {
    mockTake.mockRejectedValueOnce(new Error('camera busy'));
    const { onTaken, onError } = await renderCamera();
    await cameraReady();

    await fireEvent.press(screen.getByRole('button', { name: 'Снять' }));

    expect(onError).toHaveBeenCalled();
    expect(onTaken).not.toHaveBeenCalled();
  });

  it('reports a camera that did not start', async () => {
    const { onError } = await renderCamera();

    await act(() => {
      mockCamera.props.onMountError?.();
    });

    expect(onError).toHaveBeenCalled();
  });

  it('closes on cancel', async () => {
    const { onCancel } = await renderCamera();

    await fireEvent.press(screen.getByRole('button', { name: 'Отмена' }));

    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('closes on the system back button', async () => {
    const addListener = jest.spyOn(BackHandler, 'addEventListener');
    try {
      const { onCancel } = await renderCamera();
      const onBack = addListener.mock.calls.at(-1)?.[1];

      expect(onBack?.({ type: 'hardwareBackPress', timeStamp: 0 })).toBe(true);
      expect(onCancel).toHaveBeenCalledTimes(1);
    } finally {
      addListener.mockRestore();
    }
  });

  it('deletes the photo when the client left during the shot', async () => {
    let finish: (value: unknown) => void = () => undefined;
    mockTake.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { onTaken, view } = await renderCamera();
    await cameraReady();

    await fireEvent.press(screen.getByRole('button', { name: 'Снять' }));
    await view.unmount();
    await act(async () => {
      finish(shot);
      await Promise.resolve();
    });

    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/out.jpg');
    expect(onTaken).not.toHaveBeenCalled();
  });
});

describe('allowSelfieCamera', () => {
  it.each([true, false])('reports granted=%s from the camera module', async (granted) => {
    mockRequestPermission.mockResolvedValueOnce({ granted });

    await expect(allowSelfieCamera()).resolves.toBe(granted);
  });
});
