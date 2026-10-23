import { sendPhoto, UploadAbortedError, UploadNetworkError, uploadTimeoutMs } from './sendPhoto';
import { FakeXhr } from './testing';

const photo = new Blob(['jpeg bytes'], { type: 'image/jpeg' });

function start(options: { onProgress?: (sent: number) => void; signal?: AbortSignal } = {}) {
  const xhr = new FakeXhr();
  const done = sendPhoto({
    url: 'http://api.test/kyc/documents/passport',
    accessToken: 'access-1',
    photo,
    createXhr: () => xhr.asXhr(),
    ...options,
  });
  return { xhr, done };
}

describe('sendPhoto', () => {
  it('puts the photo as the raw body with the access token', async () => {
    const { xhr, done } = start();
    xhr.respond(200, { status: 'not_submitted' });

    await expect(done).resolves.toEqual({
      status: 200,
      body: { status: 'not_submitted' },
      retryAfter: null,
    });
    expect(xhr.method).toBe('PUT');
    expect(xhr.url).toBe('http://api.test/kyc/documents/passport');
    expect(xhr.headers).toEqual({ Authorization: 'Bearer access-1', 'Content-Type': 'image/jpeg' });
    expect(xhr.body).toBe(photo);
  });

  it('reports the share of the photo sent', () => {
    const onProgress = jest.fn();
    const { xhr } = start({ onProgress });

    xhr.progress(250, 1000);
    xhr.progress(1000, 1000);

    expect(onProgress.mock.calls).toEqual([[0.25], [1]]);
  });

  it('passes error answers on, including bodies that are not JSON', async () => {
    const { xhr, done } = start();
    xhr.respond(429, 'Too Many Requests', { 'Retry-After': '30' });

    await expect(done).resolves.toEqual({ status: 429, body: null, retryAfter: '30' });
  });

  it('fails with a network error when the connection drops', async () => {
    const { xhr, done } = start();
    xhr.fail();

    await expect(done).rejects.toBeInstanceOf(UploadNetworkError);
  });

  it('gives up on a connection that went silent', async () => {
    const { xhr, done } = start();
    expect(xhr.timeout).toBe(uploadTimeoutMs);

    xhr.expire();

    await expect(done).rejects.toBeInstanceOf(UploadNetworkError);
  });

  it('stops the request when the signal is aborted', async () => {
    const controller = new AbortController();
    const { xhr, done } = start({ signal: controller.signal });

    controller.abort();

    await expect(done).rejects.toBeInstanceOf(UploadAbortedError);
    expect(xhr.aborted).toBe(true);
  });

  it('does not start when already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    const { xhr, done } = start({ signal: controller.signal });

    await expect(done).rejects.toBeInstanceOf(UploadAbortedError);
    expect(xhr.method).toBe('');
  });
});
