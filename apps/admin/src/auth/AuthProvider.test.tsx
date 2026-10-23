import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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
});
