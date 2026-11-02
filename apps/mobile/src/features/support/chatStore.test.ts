import { ChatStore } from './chatStore';
import { fakeSupportApi, message, offline, ok, page } from './testing';

const mockDiscard = jest.fn();
jest.mock('../kyc/photos', () => ({
  discardPhoto: (uri: string) => {
    mockDiscard(uri);
  },
}));

function storeWith(api: ReturnType<typeof fakeSupportApi>, now?: () => number) {
  let id = 0;
  return new ChatStore(
    api,
    () => {
      id += 1;
      return `id-${String(id)}`;
    },
    now,
  );
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

beforeEach(() => {
  mockDiscard.mockClear();
});

describe('ChatStore', () => {
  it('asks only for what is newer than the last seen message', async () => {
    const api = fakeSupportApi();
    const seen = message();
    api.messages.mockResolvedValueOnce(page([seen]));
    const store = storeWith(api);

    await store.poll();
    await store.poll();

    expect(api.messages).toHaveBeenLastCalledWith(seen.seq);
    expect(store.getState().messages).toEqual([seen]);
  });

  it('does not skip an answer that a saved message of the client overtook', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    const answer = message();
    const mine = message({ author: 'client' });
    api.sendText.mockResolvedValueOnce({ ok: true, value: mine });

    await store.send('Hello', null);
    expect(api.messages).not.toHaveBeenCalled();
    api.messages.mockResolvedValueOnce(page([answer, mine]));
    await store.poll();

    expect(api.messages).toHaveBeenCalledWith(0);
    expect(store.getState().messages).toEqual([answer, mine]);
  });

  it('keeps an unsent message visible and resends it with the same id', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    api.sendText.mockResolvedValueOnce(offline);

    await store.send('Hello', 'rental-1');

    expect(store.getState().outgoing).toEqual([
      expect.objectContaining({ id: 'id-1', text: 'Hello', state: 'waiting' }),
    ]);
    expect(store.getState().error).toEqual({ kind: 'network' });

    await store.poll();

    expect(api.sendText).toHaveBeenCalledTimes(2);
    expect(api.sendText).toHaveBeenLastCalledWith('Hello', 'rental-1', 'id-1');
    expect(store.getState().outgoing).toEqual([]);
    expect(store.getState().messages).toHaveLength(1);
  });

  it('sends in order and stops at the first that fails', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    api.sendText.mockResolvedValueOnce(offline);

    await store.send('First', null);
    await store.send('Second', null);

    expect(api.sendText.mock.calls.map(([text]) => text)).toEqual(['First', 'First', 'Second']);
    expect(store.getState().outgoing).toEqual([]);
  });

  it('removes a sent photo from the device', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);

    await store.sendPhoto('file:///cache/a.jpg', null);

    expect(api.sendPhoto).toHaveBeenCalledWith('file:///cache/a.jpg', null, 'id-1');
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/a.jpg');
  });

  it('does not resend a photo the server refused and lets the client drop it', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    api.sendPhoto.mockResolvedValueOnce({ ok: false, error: { kind: 'tooLarge' } });

    await store.sendPhoto('file:///cache/a.jpg', null);
    await store.poll();

    expect(api.sendPhoto).toHaveBeenCalledTimes(1);
    expect(store.getState().outgoing[0]?.state).toBe('refused');

    store.remove('id-1');

    expect(store.getState().outgoing).toEqual([]);
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/a.jpg');
  });

  it('starts over when support closed the conversation and a new one opened', async () => {
    const api = fakeSupportApi();
    const fresh = message();
    api.messages.mockResolvedValueOnce(page([message()], 'c-1'));
    api.messages.mockResolvedValueOnce(page([fresh], 'c-2'));
    const store = storeWith(api);

    await store.poll();
    await store.poll();

    expect(store.getState().messages).toEqual([fresh]);
  });

  it('forgets everything on sign-out', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    api.sendPhoto.mockResolvedValueOnce(offline);
    await store.sendPhoto('file:///cache/a.jpg', null);

    store.reset();

    expect(store.getState().outgoing).toEqual([]);
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/a.jpg');
  });

  it('keeps the rental of a photo sent from a trip', async () => {
    const api = fakeSupportApi();

    await storeWith(api).sendPhoto('file:///cache/a.jpg', 'rental-1');

    expect(api.sendPhoto).toHaveBeenCalledWith('file:///cache/a.jpg', 'rental-1', 'id-1');
  });

  it('drops the answer to a poll that outlived the sign-out', async () => {
    const api = fakeSupportApi();
    const late = deferred<ReturnType<typeof page>>();
    api.messages.mockReturnValueOnce(late.promise);
    const store = storeWith(api);

    const polling = store.poll();
    store.reset();
    late.resolve(page([message({ seq: 500 })]));
    await polling;
    await store.poll();

    expect(store.getState().messages).toEqual([]);
    expect(api.messages).toHaveBeenLastCalledWith(0);
  });

  it('drops a message delivered after the sign-out', async () => {
    const api = fakeSupportApi();
    const late = deferred<Awaited<ReturnType<typeof api.sendText>>>();
    api.sendText.mockReturnValueOnce(late.promise);
    const store = storeWith(api);

    const sending = store.send('Hello', null);
    store.reset();
    late.resolve(ok(message({ author: 'client' })));
    await sending;

    expect(store.getState().messages).toEqual([]);
  });

  it('waits between attempts the server failed, longer each time', async () => {
    const api = fakeSupportApi();
    let time = 0;
    const store = storeWith(api, () => time);
    api.sendText.mockResolvedValue({ ok: false, error: { kind: 'unknown' } });

    await store.send('Hello', null);
    await store.resend();
    expect(api.sendText).toHaveBeenCalledTimes(1);

    time = 4_000;
    await store.resend();
    expect(api.sendText).toHaveBeenCalledTimes(2);

    time = 8_000;
    await store.resend();
    expect(api.sendText).toHaveBeenCalledTimes(2);

    time = 12_000;
    await store.resend();
    expect(api.sendText).toHaveBeenCalledTimes(3);
  });

  it('waits as long as the rate limit says', async () => {
    const api = fakeSupportApi();
    let time = 0;
    const store = storeWith(api, () => time);
    api.sendText.mockResolvedValueOnce({
      ok: false,
      error: { kind: 'rateLimited', retryAfter: 60 },
    });

    await store.send('Hello', null);
    time = 59_000;
    await store.resend();
    expect(api.sendText).toHaveBeenCalledTimes(1);

    time = 60_000;
    await store.resend();
    expect(store.getState().outgoing).toEqual([]);
  });

  it('does not pause while offline: such an attempt costs the server nothing', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api, () => 0);
    api.sendText.mockResolvedValueOnce(offline);

    await store.send('Hello', null);
    await store.resend();

    expect(store.getState().outgoing).toEqual([]);
  });

  it('gives up on a message the server rejected and sends the next one', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    api.sendText.mockResolvedValueOnce({ ok: false, error: { kind: 'rejected' } });

    await store.send('First', 'rental-gone');
    await store.send('Second', null);

    expect(store.getState().outgoing.map((item) => item.state)).toEqual(['refused']);
    expect(api.sendText).toHaveBeenLastCalledWith('Second', null, 'id-2');
  });
});
