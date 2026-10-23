import { Platform } from 'react-native';

import { canonicalPath, discardPhoto, pickPhoto } from './photos';

const mockDeleted: string[] = [];
const mockCache = { uri: 'file:///cache/' };
const mockResize = jest.fn();
const mockSave = jest.fn<Promise<{ uri: string }>, [unknown]>(() =>
  Promise.resolve({ uri: 'file:///cache/out.jpg' }),
);

jest.mock('expo-file-system', () => ({
  Paths: {
    get cache() {
      return mockCache;
    },
  },
  File: class {
    readonly uri: string;
    constructor(uri: string) {
      this.uri = uri;
    }
    delete() {
      mockDeleted.push(this.uri);
    }
  },
}));

jest.mock('expo-image-manipulator', () => ({
  SaveFormat: { JPEG: 'jpeg' },
  ImageManipulator: {
    manipulate: () => ({
      resize: mockResize,
      release: jest.fn(),
      renderAsync: () => Promise.resolve({ saveAsync: mockSave, release: jest.fn() }),
    }),
  },
}));

jest.mock('expo-image-picker', () => ({
  CameraType: { front: 'front', back: 'back' },
  requestCameraPermissionsAsync: jest.fn(),
  launchCameraAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));

// The mocked module, typed for the calls the tests check.
const picker = jest.requireMock<{
  requestCameraPermissionsAsync: jest.Mock;
  launchCameraAsync: jest.Mock;
  launchImageLibraryAsync: jest.Mock;
}>('expo-image-picker');

const asset = (width: number, height: number) => ({
  canceled: false,
  assets: [{ uri: 'file:///cache/ImagePicker/shot.heic', width, height }],
});

describe('pickPhoto', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockDeleted.length = 0;
    picker.requestCameraPermissionsAsync.mockResolvedValue({ granted: true });
  });

  it('takes a photo without EXIF and keeps only a JPEG copy', async () => {
    picker.launchCameraAsync.mockResolvedValue(asset(1200, 1600));

    await expect(pickPhoto('camera')).resolves.toEqual({
      kind: 'picked',
      uri: 'file:///cache/out.jpg',
    });
    expect(picker.launchCameraAsync).toHaveBeenCalledWith({
      mediaTypes: ['images'],
      quality: 1,
      exif: false,
    });
    expect(mockResize).not.toHaveBeenCalled();
    expect(mockSave).toHaveBeenCalledWith({ format: 'jpeg', compress: 0.85 });
    // The picker's own copy is gone; the JPEG is the caller's to discard after sending.
    expect(mockDeleted).toEqual(['file:///cache/ImagePicker/shot.heic']);
  });

  it('opens the front camera for the selfie', async () => {
    picker.launchCameraAsync.mockResolvedValue(asset(1200, 1600));

    await pickPhoto('camera', { selfie: true });

    expect(picker.launchCameraAsync).toHaveBeenCalledWith(
      expect.objectContaining({ cameraType: 'front' }),
    );
  });

  it('scales a large photo down along its longer side', async () => {
    picker.launchImageLibraryAsync.mockResolvedValue(asset(3000, 4000));

    await pickPhoto('library');

    expect(mockResize).toHaveBeenCalledWith({ height: 2560 });
  });

  it('reports a denied camera without opening it', async () => {
    picker.requestCameraPermissionsAsync.mockResolvedValue({ granted: false });

    await expect(pickPhoto('camera')).resolves.toEqual({ kind: 'cameraDenied' });
    expect(picker.launchCameraAsync).not.toHaveBeenCalled();
  });

  it('reports a cancelled picker', async () => {
    picker.launchImageLibraryAsync.mockResolvedValue({ canceled: true, assets: null });

    await expect(pickPhoto('library')).resolves.toEqual({ kind: 'canceled' });
  });

  it('still deletes the picker copy when the conversion fails', async () => {
    picker.launchImageLibraryAsync.mockResolvedValue(asset(1200, 1600));
    mockSave.mockRejectedValueOnce(new Error('decode failed'));

    await expect(pickPhoto('library')).rejects.toThrow('decode failed');
    expect(mockDeleted).toEqual(['file:///cache/ImagePicker/shot.heic']);
  });
});

describe('discardPhoto', () => {
  beforeEach(() => {
    mockDeleted.length = 0;
    mockCache.uri = 'file:///cache/';
  });

  it('deletes files in the app cache only', () => {
    discardPhoto('file:///cache/ImageManipulator/out.jpg');
    discardPhoto('file:///storage/DCIM/Camera/original.jpg');

    expect(mockDeleted).toEqual(['file:///cache/ImageManipulator/out.jpg']);
  });

  it.each([
    // iOS reports the same container through the /private symlink or without it.
    [
      'file:///private/var/mobile/Containers/Data/Application/A1/Library/Caches/',
      'file:///var/mobile/Containers/Data/Application/A1/Library/Caches/ImagePicker/x.jpg',
    ],
    [
      'file:///var/mobile/Containers/Data/Application/A1/Library/Caches',
      '/private/var/mobile/Containers/Data/Application/A1/Library/Caches/ImagePicker/x.jpg',
    ],
    // Android: /data/data/<package> links to /data/user/0/<package>.
    [
      'file:///data/user/0/by.flagman.drive/cache/',
      'file:///data/data/by.flagman.drive/cache/x.jpg',
    ],
    [
      'file:///data/user/0/by.flagman.drive/cache/',
      'file:/data/user/0/by.flagman.drive/cache/x.jpg',
    ],
    [
      'file:///data/user/0/by.flagman.drive/cache/',
      'file:///data/user/0/by.flagman.drive/cache/a%20b.jpg',
    ],
  ])('recognises the cache %s in another spelling: %s', (cache, uri) => {
    mockCache.uri = cache;

    discardPhoto(uri);

    expect(mockDeleted).toEqual([uri]);
  });

  it.each([
    'file:///cache/../storage/DCIM/original.jpg',
    'file:///cache-other/x.jpg',
    'content://media/external/images/media/42',
  ])('leaves %s alone', (uri) => {
    discardPhoto(uri);

    expect(mockDeleted).toEqual([]);
  });

  it('releases a blob URL on the web', () => {
    const os = Platform.OS;
    const revoke = jest.fn();
    const original = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = revoke;
    Platform.OS = 'web';
    try {
      discardPhoto('blob:http://localhost:8081/1234');
    } finally {
      Platform.OS = os;
      URL.revokeObjectURL = original;
    }

    expect(revoke).toHaveBeenCalledWith('blob:http://localhost:8081/1234');
    expect(mockDeleted).toEqual([]);
  });
});

describe('canonicalPath', () => {
  it('rejects what is not a plain absolute path', () => {
    expect(canonicalPath('content://media/1')).toBeNull();
    expect(canonicalPath('file:///a/../b')).toBeNull();
    expect(canonicalPath('file:///a/%E0%A4%A')).toBeNull();
    expect(canonicalPath('file:///private/var/x')).toBe('/var/x');
  });
});
