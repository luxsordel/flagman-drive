import { readPhoto } from './readPhoto';

const mockBytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
const mockOpened: string[] = [];

jest.mock('expo-file-system', () => ({
  File: class {
    constructor(uri: string) {
      // expo-file-system validates the path in the constructor and throws synchronously.
      if (!uri.startsWith('file://')) throw new Error(`Invalid path: ${uri}`);
      mockOpened.push(uri);
    }
    bytes() {
      return Promise.resolve(mockBytes);
    }
  },
}));

describe('readPhoto', () => {
  beforeEach(() => {
    mockOpened.length = 0;
  });

  it('returns the file bytes rather than a Blob', async () => {
    await expect(readPhoto('file:///cache/ImageManipulator/out.jpg')).resolves.toBe(mockBytes);
    expect(mockOpened).toEqual(['file:///cache/ImageManipulator/out.jpg']);
  });

  it('rejects instead of throwing when the path is refused', async () => {
    let result: Promise<unknown> | undefined;
    expect(() => {
      result = readPhoto('not a path');
    }).not.toThrow();

    await expect(result).rejects.toThrow('Invalid path');
  });
});
