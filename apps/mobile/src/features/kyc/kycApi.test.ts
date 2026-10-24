import { fakeApi, json, problem } from '../auth/session/testing';
import { createKycApi } from './kycApi';
import type { PhotoBody } from './sendPhoto';
import { FakeXhr, kycApplication } from './testing';

const photo = new Blob(['jpeg bytes'], { type: 'image/jpeg' });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(token: string | null = 'access-1') {
  const http = fakeApi();
  const xhr = new FakeXhr();
  const loadPhoto = jest.fn<Promise<PhotoBody>, [string]>(() => Promise.resolve(photo));
  const kyc = createKycApi({
    api: http.client,
    baseUrl: 'http://api.test/',
    getAccessToken: () => Promise.resolve(token),
    loadPhoto,
    createXhr: () => xhr.asXhr(),
  });
  return { http, xhr, kyc, loadPhoto };
}

describe('createKycApi', () => {
  it('reads the application', async () => {
    const { http, kyc } = setup();
    const application = kycApplication(['passport']);
    http.on('/kyc/application', json(application));

    await expect(kyc.getApplication()).resolves.toEqual({ ok: true, application });
  });

  it('explains a failed submit', async () => {
    const { http, kyc } = setup();
    http.on('/kyc/application/submit', problem('kyc-incomplete', 409));

    await expect(kyc.submit()).resolves.toEqual({ ok: false, error: { kind: 'incomplete' } });
  });

  it('reports a network error when the API cannot be reached', async () => {
    const { http, kyc } = setup();
    http.on('/kyc/application', new TypeError('Network request failed'));

    await expect(kyc.getApplication()).resolves.toEqual({
      ok: false,
      error: { kind: 'network' },
    });
  });

  it('uploads the photo and returns the updated application', async () => {
    const { xhr, kyc, loadPhoto } = setup();
    const onProgress = jest.fn();
    const application = kycApplication(['license_front']);

    const upload = kyc.upload('license_front', 'file:///cache/photo.jpg', onProgress);
    await flush();
    xhr.progress(1, 2);
    xhr.respond(200, application);

    await expect(upload.done).resolves.toEqual({ ok: true, application });
    expect(loadPhoto).toHaveBeenCalledWith('file:///cache/photo.jpg');
    expect(xhr.url).toBe('http://api.test/kyc/documents/license_front');
    expect(xhr.body).toBe(photo);
    expect(onProgress).toHaveBeenCalledWith(0.5);
  });

  it('sends the bytes a phone reads as they are, not wrapped in a Blob', async () => {
    const { xhr, kyc, loadPhoto } = setup();
    const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
    loadPhoto.mockResolvedValueOnce(bytes);

    const upload = kyc.upload('passport', 'file:///cache/photo.jpg');
    await flush();
    xhr.respond(200, kycApplication(['passport']));

    await expect(upload.done).resolves.toMatchObject({ ok: true });
    expect(xhr.body).toBe(bytes);
    expect(xhr.headers['Content-Type']).toBe('image/jpeg');
  });

  it('explains an upload the API refused', async () => {
    const { xhr, kyc } = setup();

    const upload = kyc.upload('passport', 'file:///cache/photo.jpg');
    await flush();
    xhr.respond(415, { type: 'urn:flagman:problem:document-type-unsupported' });

    await expect(upload.done).resolves.toEqual({ ok: false, error: { kind: 'unsupported' } });
  });

  it('reports a dropped connection as a network error', async () => {
    const { xhr, kyc } = setup();

    const upload = kyc.upload('passport', 'file:///cache/photo.jpg');
    await flush();
    xhr.fail();

    await expect(upload.done).resolves.toEqual({ ok: false, error: { kind: 'network' } });
  });

  it('ends quietly when the client cancels', async () => {
    const { xhr, kyc } = setup();

    const upload = kyc.upload('passport', 'file:///cache/photo.jpg');
    await flush();
    upload.abort();

    await expect(upload.done).resolves.toEqual({ ok: false, error: null });
    expect(xhr.aborted).toBe(true);
  });

  it('does not send anything once signed out', async () => {
    const { xhr, kyc } = setup(null);

    const upload = kyc.upload('passport', 'file:///cache/photo.jpg');

    await expect(upload.done).resolves.toEqual({ ok: false, error: { kind: 'sessionExpired' } });
    expect(xhr.method).toBe('');
  });

  it('asks for another photo when this one cannot be read', async () => {
    const { xhr, kyc, loadPhoto } = setup();
    loadPhoto.mockRejectedValueOnce(new Error('ENOENT'));

    const upload = kyc.upload('passport', 'file:///cache/gone.jpg');

    await expect(upload.done).resolves.toEqual({ ok: false, error: { kind: 'unsupported' } });
    expect(xhr.method).toBe('');
  });
});
