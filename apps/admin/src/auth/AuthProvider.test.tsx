import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { fakeStaffAuthApi, testStaff } from '../test/auth';
import { AuthProvider } from './AuthProvider';
import { useAuth, type AuthContextValue } from './context';
import { createStaffSession } from './session';

describe('AuthProvider', () => {
  it('keeps signOut the same function while the auth state changes', async () => {
    const seen: AuthContextValue['signOut'][] = [];
    function Probe() {
      const { state, signOut } = useAuth();
      seen.push(signOut);
      return <p>{state.status}</p>;
    }

    render(
      <AuthProvider session={createStaffSession(fakeStaffAuthApi(testStaff()))}>
        <Probe />
      </AuthProvider>,
    );

    expect(await screen.findByText('signedIn')).toBeDefined();
    expect(new Set(seen).size).toBe(1);
  });

  it('signs in with the token of a passed sign-in', async () => {
    const api = fakeStaffAuthApi(null);
    api.me.mockResolvedValue(testStaff('technician'));
    const auth = await renderProbe(api, 'signedOut');

    await act(() => auth().signIn({ accessToken: 'access-1', expiresInSeconds: 900 }));

    expect(screen.getByText('technician')).toBeDefined();
    expect(api.me).toHaveBeenCalledWith('access-1');
  });

  it('stays signed out and rejects when the profile does not load', async () => {
    const api = fakeStaffAuthApi(null);
    const auth = await renderProbe(api, 'signedOut');

    await expect(
      act(() => auth().signIn({ accessToken: 'access-1', expiresInSeconds: 900 })),
    ).rejects.toThrow('no session');
    expect(screen.getByText('signedOut')).toBeDefined();
  });

  it('tells a sign-out from a session that ended by itself', async () => {
    const api = fakeStaffAuthApi(testStaff());
    const auth = await renderProbe(api, 'admin');

    await act(() => auth().signOut());

    expect(screen.getByText('signedOut byUser')).toBeDefined();
  });

  it('does not take an expired session for a sign-out', async () => {
    // Real time still runs for findBy*; the refresh timer is driven by hand.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = fakeStaffAuthApi(testStaff());
    api.refresh
      .mockResolvedValueOnce({ accessToken: 'access-1', expiresInSeconds: 900 })
      .mockResolvedValue('rejected');
    await renderProbe(api, 'admin');

    await act(() => vi.advanceTimersByTimeAsync(840_000));

    expect(screen.getByText('signedOut')).toBeDefined();
    expect(api.logout).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});

/** Renders a probe that prints the auth state; resolves once it shows `ready`. */
async function renderProbe(api: ReturnType<typeof fakeStaffAuthApi>, ready: string) {
  let auth: AuthContextValue | null = null;
  function Probe() {
    auth = useAuth();
    const { state } = auth;
    if (state.status === 'signedIn') return <p>{state.staff.role}</p>;
    return (
      <p>{state.status === 'signedOut' && state.byUser ? 'signedOut byUser' : state.status}</p>
    );
  }
  render(
    <AuthProvider session={createStaffSession(api)}>
      <Probe />
    </AuthProvider>,
  );
  await screen.findByText(ready);
  return (): AuthContextValue => {
    if (!auth) throw new Error('the probe has not rendered');
    return auth;
  };
}
