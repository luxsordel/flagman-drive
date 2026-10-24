import type { PhotoBody } from './sendPhoto';

/**
 * The picked photo on the web is a blob or data URI; the browser sends a Blob as is.
 * Typecheck reads only readPhoto.ts, so both files declare the same `PhotoBody` result.
 */
export async function readPhoto(uri: string): Promise<PhotoBody> {
  const response = await fetch(uri);
  return response.blob();
}
