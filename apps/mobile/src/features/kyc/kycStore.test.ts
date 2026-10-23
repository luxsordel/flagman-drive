import { KycStore } from './kycStore';
import { fakeKycApi, kycApplication, ok } from './testing';

function setup() {
  const { api, uploads } = fakeKycApi();
  return { api, uploads, store: new KycStore(api) };
}

describe('KycStore', () => {
  it('loads the application once for parallel callers', async () => {
    const { api, store } = setup();
    const application = kycApplication();
    api.getApplication.mockResolvedValue(ok(application));

    await Promise.all([store.load(), store.load()]);

    expect(api.getApplication).toHaveBeenCalledTimes(1);
    expect(store.getState()).toEqual({ application, loading: false, loadError: null });
  });

  it('keeps the last application when a reload fails', async () => {
    const { api, store } = setup();
    const application = kycApplication();
    api.getApplication
      .mockResolvedValueOnce(ok(application))
      .mockResolvedValueOnce({ ok: false, error: { kind: 'network' } });

    await store.load();
    await store.load();

    expect(store.getState()).toEqual({
      application,
      loading: false,
      loadError: { kind: 'network' },
    });
  });

  it('takes the application from a finished upload', async () => {
    const { uploads, store } = setup();
    const updated = kycApplication(['passport']);

    const upload = store.upload('passport', 'file:///cache/a.jpg');
    uploads[0]?.finish(ok(updated));

    await expect(upload.done).resolves.toEqual(ok(updated));
    expect(store.getState().application).toEqual(updated);
  });

  it('reloads when the application was locked meanwhile', async () => {
    const { api, uploads, store } = setup();
    const pending = kycApplication([], { status: 'pending' });
    api.getApplication.mockResolvedValue(ok(pending));

    const upload = store.upload('passport', 'file:///cache/a.jpg');
    uploads[0]?.finish({ ok: false, error: { kind: 'locked' } });
    await upload.done;

    expect(store.getState().application?.status).toBe('pending');
  });

  it('submits and follows the new status', async () => {
    const { api, store } = setup();
    const pending = kycApplication(
      ['license_front', 'license_back', 'passport', 'selfie_with_license'],
      { status: 'pending' },
    );
    api.submit.mockResolvedValue(ok(pending));

    await expect(store.submit()).resolves.toBeNull();
    expect(store.getState().application).toEqual(pending);
  });

  it('reloads after a submit refused as incomplete', async () => {
    const { api, store } = setup();
    api.submit.mockResolvedValue({ ok: false, error: { kind: 'incomplete' } });
    api.getApplication.mockResolvedValue(ok(kycApplication(['passport'])));

    await expect(store.submit()).resolves.toEqual({ kind: 'incomplete' });
    expect(api.getApplication).toHaveBeenCalledTimes(1);
  });

  it('forgets the application at reset and ignores answers for the previous client', async () => {
    const { api, uploads, store } = setup();
    let answer: (result: ReturnType<typeof ok>) => void = () => undefined;
    api.getApplication.mockReturnValueOnce(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );

    const loading = store.load();
    const upload = store.upload('passport', 'file:///cache/a.jpg');
    store.reset();
    answer(ok(kycApplication(['license_front'])));
    uploads[0]?.finish(ok(kycApplication(['passport'])));
    await Promise.all([loading, upload.done]);

    expect(store.getState()).toEqual({ application: null, loading: false, loadError: null });
  });

  it('notifies subscribers', async () => {
    const { api, store } = setup();
    api.getApplication.mockResolvedValue(ok(kycApplication()));
    const listener = jest.fn();
    const unsubscribe = store.subscribe(listener);

    await store.load();
    unsubscribe();
    store.reset();

    // Once for "loading", once for the answer; nothing after unsubscribing.
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
