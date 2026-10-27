import { act, render, waitFor } from '@testing-library/react-native';

import { json, testSession, tokens } from '../auth/session/testing';
import { PushListeners } from './PushListeners';
import { fakeNotificationsDeps, fakePushDeps } from './testing';

const mockRouter = { push: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));

let mockResponse: unknown = null;
const mockTokenListeners: (() => void)[] = [];
const mockRemoveTokenListener = jest.fn();
jest.mock('expo-notifications', () => ({
  setNotificationHandler: jest.fn(),
  useLastNotificationResponse: () => mockResponse,
  addPushTokenListener: (listener: () => void) => {
    mockTokenListeners.push(listener);
    return { remove: mockRemoveTokenListener };
  },
}));

function tapped(identifier: string, data: unknown) {
  return { notification: { request: { identifier, content: { data } } } };
}

async function signedInSession() {
  const { api, session } = testSession({ refreshToken: 'refresh-0' });
  api.on('/auth/token/refresh', json(tokens(1)));
  api.on('/auth/logout', new Response(null, { status: 204 }));
  await session.restore();
  return session;
}

describe('PushListeners', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockResponse = null;
    mockTokenListeners.length = 0;
  });

  it('registers the phone once signed in, without asking for the permission', async () => {
    const deps = fakeNotificationsDeps();
    await render(<PushListeners session={await signedInSession()} deps={deps} />);

    await waitFor(() => {
      expect(deps.api.registerDevice).toHaveBeenCalledWith('ExponentPushToken[abc]', 'ios');
    });
    expect(deps.push.requestPermission).not.toHaveBeenCalled();
  });

  it('does nothing while signed out', async () => {
    const deps = fakeNotificationsDeps();
    const { session } = testSession();
    await session.restore();

    await render(<PushListeners session={session} deps={deps} />);

    expect(deps.push.getPermission).not.toHaveBeenCalled();
    expect(mockTokenListeners).toHaveLength(0);
  });

  it('sends a token the OS replaced', async () => {
    const push = fakePushDeps();
    const deps = fakeNotificationsDeps(push);
    await render(<PushListeners session={await signedInSession()} deps={deps} />);
    await waitFor(() => {
      expect(deps.api.registerDevice).toHaveBeenCalledTimes(1);
    });
    push.getToken.mockResolvedValue('ExponentPushToken[new]');

    await act(() => {
      for (const listener of mockTokenListeners) listener();
    });

    await waitFor(() => {
      expect(deps.api.registerDevice).toHaveBeenLastCalledWith('ExponentPushToken[new]', 'ios');
    });
  });

  it('stops listening on sign-out', async () => {
    const session = await signedInSession();
    await render(<PushListeners session={session} deps={fakeNotificationsDeps()} />);

    await act(() => session.signOut());

    expect(mockRemoveTokenListener).toHaveBeenCalledTimes(1);
  });

  it('opens the screen of a tapped notification once', async () => {
    const session = await signedInSession();
    const deps = fakeNotificationsDeps();
    mockResponse = tapped('n-1', { event: 'trip_finished', rental_id: 'r-1' });
    const view = await render(<PushListeners session={session} deps={deps} />);

    await view.rerender(<PushListeners session={session} deps={deps} />);

    expect(mockRouter.push).toHaveBeenCalledTimes(1);
    expect(mockRouter.push).toHaveBeenCalledWith('/');
  });

  it('ignores a notification the server does not send', async () => {
    mockResponse = tapped('n-2', { event: 'something_else' });

    await render(
      <PushListeners session={await signedInSession()} deps={fakeNotificationsDeps()} />,
    );

    expect(mockRouter.push).not.toHaveBeenCalled();
  });
});
