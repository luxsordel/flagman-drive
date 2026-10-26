import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fleetCar, snapshot } from '../test/fleet';
import {
  createFleetStream,
  fleetStreamUrl,
  reconnectDelaysMs,
  tokenCheckMs,
  type FleetMessage,
  type SocketLike,
  type StreamStatus,
} from './stream';

class FakeSocket implements SocketLike {
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  sent: unknown[] = [];
  closed = false;

  constructor(readonly url: string) {}

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.closed = true;
  }

  // What the server does.
  open() {
    this.onopen?.();
  }

  receive(message: FleetMessage | string) {
    this.onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) });
  }

  drop(code: number) {
    this.onclose?.({ code });
  }
}

const url = 'wss://admin.test/admin/fleet/stream';

function setUp(token: { current: string | null } = { current: 'access-1' }) {
  const sockets: FakeSocket[] = [];
  const statuses: StreamStatus[] = [];
  const messages: FleetMessage[] = [];
  const open = createFleetStream({
    url,
    accessToken: () => token.current,
    createSocket: (address) => {
      const socket = new FakeSocket(address);
      sockets.push(socket);
      return socket;
    },
  });
  const connection = open({
    onMessage: (message) => messages.push(message),
    onStatus: (status) => statuses.push(status),
  });
  const last = (): FakeSocket => {
    const socket = sockets.at(-1);
    if (!socket) throw new Error('no socket was opened');
    return socket;
  };
  return { sockets, statuses, messages, connection, last, token };
}

describe('fleet stream', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('builds the address on the panel origin', () => {
    expect(fleetStreamUrl({ protocol: 'https:', host: 'admin.flagman.by' })).toBe(
      'wss://admin.flagman.by/admin/fleet/stream',
    );
    expect(fleetStreamUrl({ protocol: 'http:', host: '127.0.0.1:5173' })).toBe(
      'ws://127.0.0.1:5173/admin/fleet/stream',
    );
  });

  it('signs in with the first message, not in the address', () => {
    const { last, statuses } = setUp();

    last().open();

    expect(last().url).toBe(url);
    expect(last().sent).toEqual([{ type: 'auth', token: 'access-1' }]);
    expect(statuses).toEqual(['connecting']);
  });

  it('is live after the snapshot and passes the messages on', () => {
    const { last, statuses, messages } = setUp();
    const fleet = snapshot([fleetCar()]);

    last().open();
    last().receive(fleet);
    last().receive('not json');

    expect(statuses).toEqual(['connecting', 'live']);
    expect(messages).toEqual([fleet]);
  });

  it('sends a renewed token without reconnecting', () => {
    const { last, sockets, token } = setUp();
    last().open();

    vi.advanceTimersByTime(tokenCheckMs);
    token.current = 'access-2';
    vi.advanceTimersByTime(tokenCheckMs);
    vi.advanceTimersByTime(tokenCheckMs);

    expect(sockets).toHaveLength(1);
    expect(last().sent).toEqual([
      { type: 'auth', token: 'access-1' },
      { type: 'auth', token: 'access-2' },
    ]);
  });

  it('reconnects after a lost connection, waiting longer each time', () => {
    const { last, sockets, statuses } = setUp();
    last().open();
    last().receive(snapshot([]));

    last().drop(1006);
    expect(statuses.at(-1)).toBe('reconnecting');
    vi.advanceTimersByTime((reconnectDelaysMs[0] ?? 0) - 1);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);

    last().drop(1013);
    vi.advanceTimersByTime(reconnectDelaysMs[0] ?? 0);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(reconnectDelaysMs[1] ?? 0);
    expect(sockets).toHaveLength(3);

    last().open();
    last().receive(snapshot([]));
    expect(statuses.at(-1)).toBe('live');
  });

  it('starts the waits over after a snapshot', () => {
    const { last, sockets } = setUp();
    last().drop(1006);
    vi.advanceTimersByTime(reconnectDelaysMs[0] ?? 0);
    last().open();
    last().receive(snapshot([]));

    last().drop(1006);
    vi.advanceTimersByTime(reconnectDelaysMs[0] ?? 0);

    expect(sockets).toHaveLength(3);
  });

  it('tries once more after an expired token, then gives the session up', () => {
    const { last, sockets, statuses } = setUp();
    last().open();

    last().drop(4401);
    expect(statuses.at(-1)).toBe('reconnecting');
    vi.advanceTimersByTime(reconnectDelaysMs[0] ?? 0);
    last().open();
    last().drop(4401);

    expect(statuses.at(-1)).toBe('signedOut');
    vi.runOnlyPendingTimers();
    expect(sockets).toHaveLength(2);
  });

  it('gives up without a session at all', () => {
    const { last, statuses } = setUp({ current: null });

    last().open();

    expect(last().closed).toBe(true);
    expect(statuses.at(-1)).toBe('signedOut');
  });

  it.each([4403, 1008])('does not retry what retrying cannot fix (%i)', (code) => {
    const { last, sockets, statuses } = setUp();
    last().open();

    last().drop(code);
    vi.runOnlyPendingTimers();

    expect(statuses.at(-1)).toBe('failed');
    expect(sockets).toHaveLength(1);
  });

  it('stops for good when closed', () => {
    const { last, sockets, connection } = setUp();
    last().open();
    last().drop(1006);

    connection.close();
    vi.runOnlyPendingTimers();

    expect(sockets).toHaveLength(1);
  });
});
