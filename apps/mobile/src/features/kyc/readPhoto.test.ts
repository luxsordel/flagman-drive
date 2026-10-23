import { readPhoto } from './readPhoto';

const mockBytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
const mockOpened: string[] = [];

jest.mock('expo-file-system', () => ({
  File: class {
    constructor(uri: string) {
      mockOpened.push(uri);
    }
    bytes() {
      return Promise.resolve(mockBytes);
    }
  },
}));

describe('readPhoto', () => {
  it('reads the file bytes, which XMLHttpRequest sends as binary on a phone', async () => {
    await expect(readPhoto('file:///cache/ImageManipulator/out.jpg')).resolves.toBe(mockBytes);
    expect(mockOpened).toEqual(['file:///cache/ImageManipulator/out.jpg']);
  });
});
