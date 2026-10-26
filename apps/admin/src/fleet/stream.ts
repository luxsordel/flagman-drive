import type { CarCommand, FleetCar, FleetRules } from './api';

/**
 * The live fleet over WebSocket (docs/adr/0017). These messages are not in OpenAPI, so their
 * types are written here by hand; the cars inside them are `StaffCarOut` of the generated client.
 */

export type CommandOutcome = 'done' | 'rejected' | 'duplicate' | 'unavailable' | 'timed_out';

/** A manual command of any staff member and how it ended. */
export interface CommandNotice {
  command_id: string;
  car_id: string;
  command: CarCommand;
  outcome: CommandOutcome;
  /** The car's reason code of a refusal, e.g. `ignition_on`. */
  reason: string | null;
  staff_id: string;
  at: string;
}

export type FleetMessage =
  | { type: 'snapshot'; items: FleetCar[]; rules: FleetRules; as_of: string }
  | { type: 'cars'; items: FleetCar[]; as_of: string }
  | { type: 'command'; command: CommandNotice };

/**
 * `live` after a snapshot; `connecting` before the first one; `reconnecting` after a lost
 * connection, while the data may be out of date; `failed` when retrying would not help;
 * `signedOut` when the session is over.
 */
export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'failed' | 'signedOut';

export interface FleetStreamHandlers {
  onMessage: (message: FleetMessage) => void;
  onStatus: (status: StreamStatus) => void;
}

export interface FleetConnection {
  close: () => void;
}

/** Opens the stream; tests put a fake here instead of a socket. */
export type OpenFleetStream = (handlers: FleetStreamHandlers) => FleetConnection;

/** The part of the browser's WebSocket the stream uses. */
export interface SocketLike {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  send: (data: string) => void;
  close: (code?: number) => void;
}

// Close codes of the API (docs/adr/0017).
const unauthorized = 4401;
const forbidden = 4403;
const policy = 1008;

/** Waits before each reconnect in a row; the last one repeats. */
export const reconnectDelaysMs: readonly number[] = [1_000, 2_000, 5_000, 10_000, 30_000];
/** How often the stream looks for a renewed token. The session renews a minute before expiry. */
export const tokenCheckMs = 15_000;

/** The browser's WebSocket behind the few calls the stream makes. */
function browserSocket(url: string): SocketLike {
  const socket = new WebSocket(url);
  const like: SocketLike = {
    onopen: null,
    onmessage: null,
    onclose: null,
    send: (data) => {
      socket.send(data);
    },
    close: (code) => {
      socket.close(code);
    },
  };
  socket.onopen = () => like.onopen?.();
  socket.onmessage = (event: MessageEvent<unknown>) => like.onmessage?.({ data: event.data });
  socket.onclose = (event) => like.onclose?.({ code: event.code });
  return like;
}

export interface FleetStreamOptions {
  url: string;
  /** The current access token; null when there is no session. */
  accessToken: () => string | null;
  createSocket?: (url: string) => SocketLike;
}

/** The address of the stream on the panel's own origin: the dev server proxies it to the API. */
export function fleetStreamUrl(location: Pick<Location, 'protocol' | 'host'>): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/admin/fleet/stream`;
}

function parse(data: unknown): FleetMessage | null {
  if (typeof data !== 'string') return null;
  try {
    const message: unknown = JSON.parse(data);
    const type =
      typeof message === 'object' && message !== null && 'type' in message ? message.type : null;
    return type === 'snapshot' || type === 'cars' || type === 'command'
      ? (message as FleetMessage)
      : null;
  } catch {
    return null;
  }
}

/**
 * Keeps one socket open: signs in with the first message, sends each renewed token, and
 * reconnects after a lost connection; every reconnect starts from a fresh snapshot.
 */
export function createFleetStream({
  url,
  accessToken,
  createSocket = browserSocket,
}: FleetStreamOptions): OpenFleetStream {
  return ({ onMessage, onStatus }) => {
    let socket: SocketLike | null = null;
    let closed = false;
    let attempt = 0;
    // A 4401 right after signing in again means the session itself is over.
    let unauthorizedInARow = 0;
    let sentToken: string | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let tokenTimer: ReturnType<typeof setInterval> | undefined;

    const sendToken = (token: string): void => {
      sentToken = token;
      socket?.send(JSON.stringify({ type: 'auth', token }));
    };

    const scheduleReconnect = (): void => {
      onStatus('reconnecting');
      const delay = reconnectDelaysMs[Math.min(attempt, reconnectDelaysMs.length - 1)] ?? 0;
      attempt += 1;
      reconnectTimer = setTimeout(connect, delay);
    };

    function connect(): void {
      const current = createSocket(url);
      socket = current;
      current.onopen = () => {
        const token = accessToken();
        if (token === null) {
          closed = true;
          current.close();
          onStatus('signedOut');
          return;
        }
        sendToken(token);
        tokenTimer = setInterval(() => {
          const renewed = accessToken();
          if (renewed !== null && renewed !== sentToken) sendToken(renewed);
        }, tokenCheckMs);
      };
      current.onmessage = (event) => {
        const message = parse(event.data);
        if (!message) return;
        if (message.type === 'snapshot') {
          attempt = 0;
          unauthorizedInARow = 0;
          onStatus('live');
        }
        onMessage(message);
      };
      current.onclose = ({ code }) => {
        clearInterval(tokenTimer);
        if (closed || socket !== current) return;
        socket = null;
        if (code === unauthorized) {
          unauthorizedInARow += 1;
          if (unauthorizedInARow > 1 || accessToken() === null) {
            onStatus('signedOut');
            return;
          }
          // The token may have expired a moment before the session renewed it: try once more.
          scheduleReconnect();
          return;
        }
        if (code === forbidden || code === policy) {
          onStatus('failed');
          return;
        }
        scheduleReconnect();
      };
    }

    onStatus('connecting');
    connect();

    return {
      close() {
        closed = true;
        clearTimeout(reconnectTimer);
        clearInterval(tokenTimer);
        socket?.close();
        socket = null;
      },
    };
  };
}
