import { File } from 'expo-file-system';

/**
 * The photo's bytes for the upload body. A Blob from `fetch(file://…)` is assembled from parts that
 * React Native's XMLHttpRequest does not send as binary, so the file is read directly instead.
 */
export function readPhoto(uri: string): Promise<Uint8Array<ArrayBuffer>> {
  return new File(uri).bytes();
}
