import type { components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import { kycErrorFromResponse, type KycError } from './kycErrors';
import { sendPhoto } from './sendPhoto';

export type KycApplication = components['schemas']['KycApplicationOut'];
export type DocumentKind = components['schemas']['DocumentKind'];
export type KycStatus = components['schemas']['KycStatus'];

export type KycResult =
  | { ok: true; application: KycApplication }
  | { ok: false; error: KycError }
  /** The client cancelled the upload: nothing to show. */
  | { ok: false; error: null };

export interface UploadHandle {
  done: Promise<KycResult>;
  abort: () => void;
}

export interface KycApi {
  getApplication(): Promise<KycResult>;
  submit(): Promise<KycResult>;
  upload(kind: DocumentKind, photoUri: string, onProgress?: (sent: number) => void): UploadHandle;
}

export interface KycApiOptions {
  /** Client that already sends the access token. */
  api: ApiClient;
  baseUrl: string;
  /** Null once signed out; throws when the API cannot be reached (see AuthSession). */
  getAccessToken: () => Promise<string | null>;
  /** Reads the picked photo; a local file URI on a phone, a blob or data URI on the web. */
  loadPhoto?: (uri: string) => Promise<Blob>;
  createXhr?: () => XMLHttpRequest;
}

type Sent<T> = Promise<{ data?: T; error?: unknown; response: Response }>;

export function createKycApi({
  api,
  baseUrl,
  getAccessToken,
  loadPhoto = loadBlob,
  createXhr,
}: KycApiOptions): KycApi {
  const origin = baseUrl.replace(/\/+$/, '');
  return {
    getApplication: () => send(api.GET('/kyc/application')),
    submit: () => send(api.POST('/kyc/application/submit')),
    upload(kind, photoUri, onProgress) {
      const controller = new AbortController();
      const { signal } = controller;
      const done = (async (): Promise<KycResult> => {
        try {
          const token = await getAccessToken();
          if (token === null) return { ok: false, error: { kind: 'sessionExpired' } };
          const photo = await loadPhoto(photoUri).catch(() => null);
          if (signal.aborted) return { ok: false, error: null };
          // The photo file vanished or is unreadable: not a network problem.
          if (photo === null) return { ok: false, error: { kind: 'unknown' } };
          const response = await sendPhoto({
            url: `${origin}/kyc/documents/${kind}`,
            accessToken: token,
            photo,
            signal,
            ...(onProgress ? { onProgress } : {}),
            ...(createXhr ? { createXhr } : {}),
          });
          if (response.status === 200) {
            return { ok: true, application: response.body as KycApplication };
          }
          return {
            ok: false,
            error: kycErrorFromResponse(response.body, response.status, response.retryAfter),
          };
        } catch {
          return signal.aborted
            ? { ok: false, error: null }
            : { ok: false, error: { kind: 'network' } };
        }
      })();
      return {
        done,
        abort: () => {
          controller.abort();
        },
      };
    },
  };
}

async function loadBlob(uri: string): Promise<Blob> {
  const response = await fetch(uri);
  return response.blob();
}

async function send(request: Sent<KycApplication>): Promise<KycResult> {
  try {
    const { data, error, response } = await request;
    if (data !== undefined) return { ok: true, application: data };
    return {
      ok: false,
      error: kycErrorFromResponse(error, response.status, response.headers.get('Retry-After')),
    };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
