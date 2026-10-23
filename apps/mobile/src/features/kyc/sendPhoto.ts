/** Raw answer to a photo upload: parsed by the caller, which knows the API's error format. */
export interface PhotoResponse {
  status: number;
  body: unknown;
  retryAfter: string | null;
}

export interface SendPhotoOptions {
  url: string;
  accessToken: string;
  photo: Blob;
  /** Share of the photo sent so far, 0..1. */
  onProgress?: (sent: number) => void;
  signal?: AbortSignal;
  createXhr?: () => XMLHttpRequest;
}

/** Generous enough for a 10 MB photo on a slow mobile network. */
export const uploadTimeoutMs = 120_000;

export class UploadNetworkError extends Error {
  constructor() {
    super('Photo upload failed on the network');
    this.name = 'UploadNetworkError';
  }
}

export class UploadAbortedError extends Error {
  constructor() {
    super('Photo upload aborted');
    this.name = 'UploadAbortedError';
  }
}

/**
 * Sends the photo as the raw body of `PUT` (ADR-0012). XMLHttpRequest rather than fetch: only it
 * reports upload progress, on iOS, Android and the web alike.
 */
export function sendPhoto({
  url,
  accessToken,
  photo,
  onProgress,
  signal,
  createXhr = () => new XMLHttpRequest(),
}: SendPhotoOptions): Promise<PhotoResponse> {
  return new Promise<PhotoResponse>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new UploadAbortedError());
      return;
    }
    const xhr = createXhr();
    const abort = () => {
      xhr.abort();
    };
    signal?.addEventListener('abort', abort, { once: true });
    const settle = () => {
      signal?.removeEventListener('abort', abort);
    };
    xhr.open('PUT', url);
    // A mobile connection can drop silently; without a timeout the progress bar would hang forever.
    xhr.timeout = uploadTimeoutMs;
    xhr.setRequestHeader('Authorization', `Bearer ${accessToken}`);
    // Every photo is re-encoded to JPEG before it leaves the device; the server checks anyway.
    xhr.setRequestHeader('Content-Type', 'image/jpeg');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      settle();
      resolve({
        status: xhr.status,
        body: parseJson(xhr.responseText),
        retryAfter: xhr.getResponseHeader('Retry-After'),
      });
    };
    xhr.onerror = xhr.ontimeout = () => {
      settle();
      reject(new UploadNetworkError());
    };
    xhr.onabort = () => {
      settle();
      reject(new UploadAbortedError());
    };
    xhr.send(photo);
  });
}

// A proxy or an outage can answer with HTML or nothing at all.
function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}
