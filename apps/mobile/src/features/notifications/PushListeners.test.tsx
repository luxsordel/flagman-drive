import { act, render, waitFor } from '@testing-library/react-native';

import type { AuthSession } from '../auth/session/authSession';
import { json, testSession, tokens } from '../auth/session/testing';
import type { Rental } from '../rental/rentalApi';
import type { RentalState } from '../rental/rentalStore';
import { rental } from '../rental/testing';
import type { NotificationsDeps } from './NotificationsContext';
import { PushListeners } from './PushListeners';
import { fakeNotificationsDeps, fakePushDeps, granted, notAsked } from './testing';

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

const emptyRentalState: RentalState = {
  restored: true,
  rental: null,
  offsetMs: 0,
  inspection: null,
  debts: [],
  pending: null,
  uploading: 0,
  error: null,
};

/** The part of RentalStore the listeners read; `set` changes the rental as the store would. */
function fakeRentals(initial: Rental | null = null) {
  let current = initial;
  const listeners = new Set<() => void>();
  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getState: (): RentalState => ({ ...emptyRentalState, rental: current }),
    set(next: Rental | null) {
      current = next;
      for (const listener of listeners) listener();
    },
  };
}

function listeners(
  session: AuthSession,
  deps: NotificationsDeps,
  rentals: ReturnType<typeof fakeRentals> = fakeRentals(),
) {
  return <PushListeners session={session} deps={deps} rentals={rentals} />;
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
    await render(listeners(await signedInSession(), deps));

    await waitFor(() => {
      expect(deps.api.registerDevice).toHaveBeenCalledWith('ExponentPushToken[abc]', 'ios');
    });
    expect(deps.push.requestPermission).not.toHaveBeenCalled();
  });

  it('does nothing while signed out', async () => {
    const deps = fakeNotificationsDeps();
    const { session } = testSession();
    await session.restore();

    await render(listeners(session, deps));

    expect(deps.push.getPermission).not.toHaveBeenCalled();
    expect(mockTokenListeners).toHaveLength(0);
  });

  it('sends a token the OS replaced', async () => {
    const push = fakePushDeps();
    const deps = fakeNotificationsDeps(push);
    await render(listeners(await signedInSession(), deps));
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
    await render(listeners(session, fakeNotificationsDeps()));

    await act(() => session.signOut());

    expect(mockRemoveTokenListener).toHaveBeenCalledTimes(1);
  });

  it('opens the screen of a tapped notification once', async () => {
    const session = await signedInSession();
    const deps = fakeNotificationsDeps();
    mockResponse = tapped('n-1', { event: 'reservation_expiring', rental_id: 'r-1' });
    const view = await render(listeners(session, deps));

    await view.rerender(listeners(session, deps));

    expect(mockRouter.push).toHaveBeenCalledTimes(1);
    expect(mockRouter.push).toHaveBeenCalledWith('/rental');
  });

  it('ignores a notification the server does not send', async () => {
    mockResponse = tapped('n-2', { event: 'something_else' });

    await render(listeners(await signedInSession(), fakeNotificationsDeps()));

    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it('asks for the permission once a reservation is made, once per reservation', async () => {
    const push = fakePushDeps(notAsked);
    const rentals = fakeRentals();
    await render(listeners(await signedInSession(), fakeNotificationsDeps(push), rentals));
    expect(push.requestPermission).not.toHaveBeenCalled();

    await act(() => {
      rentals.set(rental());
    });
    await act(() => {
      rentals.set(rental({ expires_at: '2026-10-26T12:41:00Z' }));
    });

    await waitFor(() => {
      expect(push.requestPermission).toHaveBeenCalledTimes(1);
    });
  });

  it('does not ask once the trip has started', async () => {
    const push = fakePushDeps(notAsked);

    await render(
      listeners(
        await signedInSession(),
        fakeNotificationsDeps(push),
        fakeRentals(rental({ status: 'in_trip' })),
      ),
    );

    expect(push.requestPermission).not.toHaveBeenCalled();
  });

  it('registers without a prompt when pushes are already allowed', async () => {
    const push = fakePushDeps(granted);

    await render(
      listeners(await signedInSession(), fakeNotificationsDeps(push), fakeRentals(rental())),
    );

    await waitFor(() => {
      expect(push.api.registerDevice).toHaveBeenCalled();
    });
    expect(push.requestPermission).not.toHaveBeenCalled();
  });
});
