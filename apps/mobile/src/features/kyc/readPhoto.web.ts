import type { PhotoBody } from './sendPhoto';

/**
 * The picked photo on the web is a blob or data URI; the browser sends a Blob as is.
 * Imports of `./readPhoto` resolve to the native file for typecheck, so this one declares the
 * shared `PhotoBody` result to stay compatible with `loadPhoto`.
 */
export async function readPhoto(uri: string): Promise<PhotoBody> {
  const response = await fetch(uri);
  return response.blob();
}
