import { createMockProfileApi, type ProfileResult } from './profileApi';
import { followProfileLanguage, ProfileStore } from './profileStore';
import { fakeProfileApi, ok, profile } from './testing';

function setup() {
  const api = fakeProfileApi();
  return { api, store: new ProfileStore(api) };
}

describe('ProfileStore', () => {
  it('loads the profile once for parallel callers', async () => {
    const { api, store } = setup();
    api.getProfile.mockResolvedValue(ok(profile()));

    await Promise.all([store.load(), store.load()]);

    expect(api.getProfile).toHaveBeenCalledTimes(1);
    expect(store.getState()).toEqual({
      profile: profile(),
      loading: false,
      loadError: null,
      saving: false,
    });
  });

  it('keeps the last profile when a reload fails', async () => {
    const { api, store } = setup();
    api.getProfile
      .mockResolvedValueOnce(ok(profile()))
      .mockResolvedValueOnce({ ok: false, error: { kind: 'network' } });

    await store.load();
    await store.load();

    expect(store.getState()).toEqual({
      profile: profile(),
      loading: false,
      loadError: { kind: 'network' },
      saving: false,
    });
  });

  it('takes the saved profile from the answer', async () => {
    const { api, store } = setup();
    const saved = profile({ first_name: 'Anna' });
    api.updateProfile.mockResolvedValue(ok(saved));

    await expect(store.save({ first_name: 'Anna' })).resolves.toEqual(ok(saved));

    expect(api.updateProfile).toHaveBeenCalledWith({ first_name: 'Anna' });
    expect(store.getState().profile).toEqual(saved);
  });

  it('returns a failed save and keeps the profile', async () => {
    const { api, store } = setup();
    api.getProfile.mockResolvedValue(ok(profile()));
    api.updateProfile.mockResolvedValue({ ok: false, error: { kind: 'invalid' } });
    await store.load();

    await expect(store.save({ first_name: 'Anna' })).resolves.toEqual({
      ok: false,
      error: { kind: 'invalid' },
    });

    expect(store.getState().profile).toEqual(profile());
  });

  it('ignores a load that left before a save and answers after it', async () => {
    const { api, store } = setup();
    let answer: (result: ProfileResult) => void = () => undefined;
    api.getProfile.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    api.updateProfile.mockResolvedValue(ok(profile({ language: 'en' })));

    const loading = store.load();
    await store.save({ language: 'en' });
    answer(ok(profile({ language: 'ru' })));
    await loading;

    expect(store.getState()).toEqual({
      profile: profile({ language: 'en' }),
      loading: false,
      loadError: null,
      saving: false,
    });
  });

  it('keeps a load that answers after a failed save', async () => {
    const { api, store } = setup();
    let answer: (result: ProfileResult) => void = () => undefined;
    api.getProfile.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    api.updateProfile.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    const loading = store.load();
    await store.save({ language: 'en' });
    answer(ok(profile()));
    await loading;

    expect(store.getState().profile).toEqual(profile());
  });

  it('says a save is in flight until the last one answers', async () => {
    const { api, store } = setup();
    const answers: ((result: ProfileResult) => void)[] = [];
    api.updateProfile.mockImplementation(
      () =>
        new Promise((resolve) => {
          answers.push(resolve);
        }),
    );

    const first = store.save({ first_name: 'Anna' });
    const second = store.save({ language: 'en' });
    expect(store.getState().saving).toBe(true);
    answers[0]?.(ok(profile()));
    await first;
    expect(store.getState().saving).toBe(true);
    answers[1]?.({ ok: false, error: { kind: 'network' } });
    await second;

    expect(store.getState().saving).toBe(false);
  });

  it('forgets the profile at sign-out', async () => {
    const { api, store } = setup();
    api.getProfile.mockResolvedValue(ok(profile()));
    await store.load();

    store.reset();

    expect(store.getState()).toEqual({
      profile: null,
      loading: false,
      loadError: null,
      saving: false,
    });
  });

  it('drops answers that arrive after sign-out', async () => {
    const { api, store } = setup();
    let answer: (result: ProfileResult) => void = () => undefined;
    api.getProfile.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    api.updateProfile.mockResolvedValue(ok(profile()));

    const loading = store.load();
    const saving = store.save({ first_name: 'Ivan' });
    store.reset();
    answer(ok(profile()));
    await Promise.all([loading, saving]);

    expect(store.getState().profile).toBeNull();
  });
});

describe('followProfileLanguage', () => {
  it('applies the language of every loaded or saved profile', async () => {
    const { api, store } = setup();
    const apply = jest.fn();
    followProfileLanguage(store, apply);
    api.getProfile.mockResolvedValue(ok(profile({ language: 'en' })));
    api.updateProfile.mockResolvedValue(ok(profile({ language: 'ru' })));

    await store.load();
    await store.save({ language: 'ru' });

    // Every state change re-applies the current language; what matters is the order.
    const applied = apply.mock.calls.map(([language]: [string]) => language);
    expect(applied.filter((language, i) => language !== applied[i - 1])).toEqual(['en', 'ru']);
  });

  it('leaves the language alone while there is no profile', () => {
    const { store } = setup();
    const apply = jest.fn();
    followProfileLanguage(store, apply);

    store.reset();

    expect(apply).not.toHaveBeenCalled();
  });
});

describe('createMockProfileApi', () => {
  it('keeps updates for later reads', async () => {
    const api = createMockProfileApi({ initial: profile(), delayMs: 0 });

    await api.updateProfile({ email: 'ivan@example.com' });

    await expect(api.getProfile()).resolves.toEqual(ok(profile({ email: 'ivan@example.com' })));
  });
});
