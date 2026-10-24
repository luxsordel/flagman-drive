import { fakeApi, json } from './testing';

// Node's built-in fetch cancels a cloned Response's stream once the clone is garbage-collected, which
// also spoils the Response it was cloned from. Collecting on demand makes that deterministic here.
// The app has no Node typings, so the two Node modules are typed by hand.
const { setFlagsFromString } = jest.requireActual<{ setFlagsFromString: (flags: string) => void }>(
  'node:v8',
);
const { runInNewContext } = jest.requireActual<{ runInNewContext: (code: string) => unknown }>(
  'node:vm',
);
setFlagsFromString('--expose-gc');
const collectGarbage = runInNewContext('gc') as () => void;

async function settleFinalizers(): Promise<void> {
  // Finalization callbacks run as separate tasks, and a clone may outlive the first collection.
  for (let pass = 0; pass < 2; pass += 1) {
    collectGarbage();
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

const accepted = json({ expires_in: 300, resend_after: 60 }, 202);

// Only plain values leave this function, so the Response copies it saw can be collected.
async function requestCode(api: ReturnType<typeof fakeApi>) {
  const { data, response } = await api.client.POST('/auth/otp/request', {
    body: { phone: '+375291234567' },
  });
  return { data, status: response.status, trace: response.headers.get('x-trace') };
}

describe('fakeApi', () => {
  it('keeps repeating a reply after earlier copies of it are garbage-collected', async () => {
    const api = fakeApi();
    api.on('/auth/otp/request', accepted);

    for (let round = 0; round < 3; round += 1) {
      const { data } = await requestCode(api);
      expect(data).toEqual({ expires_in: 300, resend_after: 60 });
      await settleFinalizers();
    }
  });

  it('serves a reply shared by several fake APIs, as module-level replies in tests are', async () => {
    for (let round = 0; round < 3; round += 1) {
      const api = fakeApi();
      api.on('/auth/otp/request', accepted);
      const { data, status } = await requestCode(api);
      expect(status).toBe(202);
      expect(data).toEqual({ expires_in: 300, resend_after: 60 });
      await settleFinalizers();
    }
  });

  it('replays the status and headers of a reply without a body', async () => {
    const api = fakeApi();
    api.on('/auth/otp/request', new Response(null, { status: 204, headers: { 'x-trace': '1' } }));

    for (let round = 0; round < 2; round += 1) {
      const { status, trace } = await requestCode(api);
      expect(status).toBe(204);
      expect(trace).toBe('1');
    }
  });
});
