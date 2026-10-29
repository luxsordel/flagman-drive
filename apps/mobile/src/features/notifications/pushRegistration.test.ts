import { enablePush, registerToken, syncPush } from './pushRegistration';
import { fakePushDeps, granted, notAsked, refused } from './testing';

describe('syncPush', () => {
  it('registers the token when pushes are allowed', async () => {
    const deps = fakePushDeps(granted, 'android');

    await expect(syncPush(deps)).resolves.toBe('enabled');
    expect(deps.api.registerDevice).toHaveBeenCalledWith('ExponentPushToken[abc]', 'android');
  });

  it('never shows the system prompt', async () => {
    const deps = fakePushDeps(notAsked);

    await expect(syncPush(deps)).resolves.toBe('askable');
    expect(deps.requestPermission).not.toHaveBeenCalled();
    expect(deps.api.registerDevice).not.toHaveBeenCalled();
  });

  it('knows when only the system settings can turn pushes on', async () => {
    await expect(syncPush(fakePushDeps(refused))).resolves.toBe('blocked');
  });

  it('does nothing on the web', async () => {
    const deps = fakePushDeps(granted, null);

    await expect(syncPush(deps)).resolves.toBe('unsupported');
    expect(deps.getPermission).not.toHaveBeenCalled();
  });

  it('fails softly without a token, e.g. on a simulator or without an EAS project', async () => {
    const deps = fakePushDeps();
    deps.getToken.mockRejectedValue(new Error('No projectId'));

    await expect(syncPush(deps)).resolves.toBe('failed');
    expect(deps.api.registerDevice).not.toHaveBeenCalled();
  });

  it('fails softly when the API refuses the token', async () => {
    const deps = fakePushDeps();
    deps.api.registerDevice.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await expect(syncPush(deps)).resolves.toBe('failed');
  });
});

describe('enablePush', () => {
  it('asks once and registers when the client agrees', async () => {
    const deps = fakePushDeps(notAsked);

    await expect(enablePush(deps)).resolves.toBe('enabled');
    expect(deps.requestPermission).toHaveBeenCalledTimes(1);
    expect(deps.api.registerDevice).toHaveBeenCalledTimes(1);
  });

  it('stays askable when the client says not now', async () => {
    const deps = fakePushDeps(notAsked);
    deps.requestPermission.mockResolvedValue(notAsked);

    await expect(enablePush(deps)).resolves.toBe('askable');
  });

  it('does not ask when the OS no longer allows it', async () => {
    const deps = fakePushDeps(refused);

    await expect(enablePush(deps)).resolves.toBe('blocked');
    expect(deps.requestPermission).not.toHaveBeenCalled();
  });

  it('does not ask again once allowed', async () => {
    const deps = fakePushDeps(granted);

    await expect(enablePush(deps)).resolves.toBe('enabled');
    expect(deps.requestPermission).not.toHaveBeenCalled();
  });
});

describe('registerToken', () => {
  it('sends a token the OS replaced', async () => {
    const deps = fakePushDeps();

    await expect(registerToken(deps, 'ExponentPushToken[new]')).resolves.toBe('enabled');
    expect(deps.api.registerDevice).toHaveBeenCalledWith('ExponentPushToken[new]', 'ios');
  });
});
