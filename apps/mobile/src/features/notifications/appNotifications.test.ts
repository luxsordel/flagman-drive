import { json, testSession, tokens } from '../auth/session/testing';
import { createAppNotifications } from './appNotifications';
import { fakeNotificationsApi, ok } from './testing';

// Nothing of it runs on sign-out; the module only has to load.
jest.mock('expo-notifications', () => ({}));

describe('createAppNotifications', () => {
  it('forgets the push device before the server ends the session', async () => {
    const order: string[] = [];
    const { api: http, session } = testSession({ refreshToken: 'refresh-0' });
    http.on('/auth/token/refresh', json(tokens(1)));
    http.on('/auth/logout', () => {
      order.push('logout');
      return new Response(null, { status: 204 });
    });
    await session.restore();
    const api = fakeNotificationsApi();
    api.forgetDevice.mockImplementation(() => {
      order.push('forgetDevice');
      return Promise.resolve(ok(null));
    });
    createAppNotifications(session, api);

    await session.signOut();

    expect(order).toEqual(['forgetDevice', 'logout']);
  });
});
