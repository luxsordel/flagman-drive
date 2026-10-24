import { File } from 'expo-file-system';

/**
 * The photo's bytes for the upload body. A Blob from `fetch(file://…)` is assembled from parts that
 * React Native's XMLHttpRequest does not send as binary, so the file is read directly instead.
 * React Native passes the bytes to native code as base64 (a third larger, encoded on the JS thread);
 * fine while photos are re-encoded and scaled down first (photos.ts), worth revisiting if not.
 *
 * Async so that a path `new File` rejects synchronously still ends up in the caller's `.catch`.
 */
export async function readPhoto(uri: string): Promise<Uint8Array<ArrayBuffer>> {
  return new File(uri).bytes();
}
