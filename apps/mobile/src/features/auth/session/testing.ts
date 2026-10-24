import { createApiClient, type TokenPair } from '@flagman/api-client';

import { AuthSession, type TokenStore } from './authSession';

/** Test doubles shared by the session and screen tests. */

export const deviceId = '7b6f0a54-3c1e-4d2a-9f5e-2b8c1d0e4f6a';

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      'content-type': status >= 400 ? 'application/problem+json' : 'application/json',
      ...headers,
    },
  });

export const problem = (name: string, status: number, headers: Record<string, string> = {}) =>
  json({ type: `urn:flagman:problem:${name}`, title: name, status }, status, headers);

export const tokens = (n: number, transport: 'body' | 'cookie' = 'body'): TokenPair => ({
  access_token: `access-${String(n)}`,
  token_type: 'Bearer',
  expires_in: 900,
  ...(transport === 'body' ? { refresh_token: `refresh-${String(n)}` } : {}),
});

type Handler = (body: unknown) => Response | Promise<Response>;

interface RecordedReply {
  status: number;
  statusText: string;
  headers: Headers;
  body: ArrayBuffer | null;
}

// Replies are often module-level constants reused across tests. They are read once and replayed as
// fresh Responses instead of cloned: Node's fetch cancels a clone's stream when the clone is
// garbage-collected, and that also marks the original as consumed, so clones fail whenever GC runs.
const recordedReplies = new WeakMap<Response, Promise<RecordedReply>>();

function replay(reply: Response): Promise<Response> {
  let recorded = recordedReplies.get(reply);
  if (recorded === undefined) {
    recorded = reply.arrayBuffer().then((buffer) => ({
      status: reply.status,
      statusText: reply.statusText,
      headers: new Headers(reply.headers),
      body: buffer.byteLength === 0 ? null : buffer,
    }));
    recordedReplies.set(reply, recorded);
  }
  return recorded.then(
    ({ status, statusText, headers, body }) =>
      new Response(body, { status, statusText, headers: new Headers(headers) }),
  );
}

export interface FakeApi {
  calls: { path: string; body: unknown }[];
  /** Replies for a path, used in order; the last one repeats. */
  on(path: string, ...replies: (Response | Handler | Error)[]): void;
}

export function fakeApi(): FakeApi & { client: ReturnType<typeof createApiClient> } {
  const calls: FakeApi['calls'] = [];
  const replies = new Map<string, (Response | Handler | Error)[]>();
  const client = createApiClient({
    baseUrl: 'http://api.test',
    fetch: async (request) => {
      const path = new URL(request.url).pathname;
      const body: unknown = await request.json().catch(() => null);
      calls.push({ path, body });
      const queue = replies.get(path);
      const reply = queue && queue.length > 1 ? queue.shift() : queue?.[0];
      if (reply === undefined) throw new Error(`No fake reply for ${path}`);
      if (reply instanceof Error) throw reply;
      return reply instanceof Response ? replay(reply) : reply(body);
    },
  });
  return {
    client,
    calls,
    on: (path, ...list) => {
      replies.set(path, list);
    },
  };
}

export function memoryStore(
  transport: 'body' | 'cookie' = 'body',
  refreshToken: string | null = null,
): TokenStore & { refreshToken: string | null } {
  const store = {
    transport,
    refreshToken,
    getRefreshToken: () => Promise.resolve(store.refreshToken),
    setRefreshToken: (token: string | null) => {
      store.refreshToken = token;
      return Promise.resolve();
    },
    getDeviceId: () => Promise.resolve(deviceId),
  };
  return store;
}

export function testSession(
  options: { transport?: 'body' | 'cookie'; refreshToken?: string | null; now?: () => number } = {},
) {
  const api = fakeApi();
  const store = memoryStore(options.transport, options.refreshToken);
  const session = new AuthSession({
    api: api.client,
    store,
    ...(options.now ? { now: options.now } : {}),
  });
  return { api, store, session };
}
