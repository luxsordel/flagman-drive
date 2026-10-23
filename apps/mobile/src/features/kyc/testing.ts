import type { DocumentKind, KycApi, KycApplication, KycResult, UploadHandle } from './kycApi';
import { documentSteps } from './kycFlow';

/** Test doubles shared by the KYC tests. */

export function kycApplication(
  uploaded: readonly DocumentKind[] = [],
  overrides: Partial<KycApplication> = {},
): KycApplication {
  return {
    status: 'not_submitted',
    rejection_reason: null,
    submitted_at: null,
    documents: uploaded.map((kind) => ({ kind, uploaded_at: '2026-10-23T10:00:00Z' })),
    missing_documents: documentSteps.filter((kind) => !uploaded.includes(kind)),
    ...overrides,
  };
}

export const ok = (application: KycApplication): KycResult => ({ ok: true, application });

interface PendingUpload {
  kind: DocumentKind;
  photoUri: string;
  onProgress: ((sent: number) => void) | undefined;
  aborted: boolean;
  finish: (result: KycResult) => void;
}

/** KycApi whose uploads stay open until the test finishes them. */
export function fakeKycApi() {
  const uploads: PendingUpload[] = [];
  const api = {
    getApplication: jest.fn<Promise<KycResult>, []>(),
    submit: jest.fn<Promise<KycResult>, []>(),
    upload: jest.fn(
      (kind: DocumentKind, photoUri: string, onProgress?: (sent: number) => void): UploadHandle => {
        let finish: (result: KycResult) => void = () => undefined;
        const done = new Promise<KycResult>((resolve) => {
          finish = resolve;
        });
        const upload: PendingUpload = { kind, photoUri, onProgress, aborted: false, finish };
        uploads.push(upload);
        return {
          done,
          abort: () => {
            upload.aborted = true;
            finish({ ok: false, error: null });
          },
        };
      },
    ),
  } satisfies KycApi;
  return { api, uploads };
}

type Listener = (event: { lengthComputable: boolean; loaded: number; total: number }) => void;

/** The part of XMLHttpRequest that `sendPhoto` uses, driven by hand. */
export class FakeXhr {
  method = '';
  url = '';
  headers: Record<string, string> = {};
  body: unknown = undefined;
  status = 0;
  responseText = '';
  responseHeaders: Record<string, string> = {};
  aborted = false;
  upload: { onprogress: Listener | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  getResponseHeader(name: string): string | null {
    return this.responseHeaders[name] ?? null;
  }

  send(body: unknown) {
    this.body = body;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total });
  }

  respond(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = typeof body === 'string' ? body : JSON.stringify(body);
    this.responseHeaders = headers;
    this.onload?.();
  }

  fail() {
    this.onerror?.();
  }

  asXhr(): XMLHttpRequest {
    return this as unknown as XMLHttpRequest;
  }
}
