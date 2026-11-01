import { ChatStore } from './chatStore';
import { fakeSupportApi, message, offline, page } from './testing';

const mockDiscard = jest.fn();
jest.mock('../kyc/photos', () => ({
  discardPhoto: (uri: string) => {
    mockDiscard(uri);
  },
}));

function storeWith(api: ReturnType<typeof fakeSupportApi>) {
  let id = 0;
  return new ChatStore(api, () => {
    id += 1;
    return `id-${String(id)}`;
  });
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

    await store.sendPhoto('file:///cache/a.jpg');

    expect(api.sendPhoto).toHaveBeenCalledWith('file:///cache/a.jpg', 'id-1');
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/a.jpg');
  });

  it('does not resend a photo the server refused and lets the client drop it', async () => {
    const api = fakeSupportApi();
    const store = storeWith(api);
    api.sendPhoto.mockResolvedValueOnce({ ok: false, error: { kind: 'tooLarge' } });

    await store.sendPhoto('file:///cache/a.jpg');
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
    await store.sendPhoto('file:///cache/a.jpg');

    store.reset();

    expect(store.getState().outgoing).toEqual([]);
    expect(mockDiscard).toHaveBeenCalledWith('file:///cache/a.jpg');
  });
});
