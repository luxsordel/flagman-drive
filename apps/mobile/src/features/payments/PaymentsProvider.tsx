import { createApiClient } from '@flagman/api-client';
import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';
import { createContext, useContext, type ReactNode } from 'react';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import type { BindingDeps } from './cardBinding';
import { createPaymentsApi } from './paymentsApi';

/** The route the provider's page sends the client back to (app/payment-methods.tsx). */
export const paymentMethodsPath = 'payment-methods';

export type PaymentsDeps = BindingDeps;

export function createAppPayments(session: AuthSession): PaymentsDeps {
  const api = createApiClient({ baseUrl: appApiUrl(), getAccessToken: session.getAccessToken });
  return {
    api: createPaymentsApi(api),
    // flagmandrive:// in a build, exp:// in Expo Go, the page's own origin on the web: the API
    // accepts each of them (docs/adr/0019).
    returnUrl: Linking.createURL(paymentMethodsPath),
    openBrowser: async (pageUrl, returnUrl) => {
      const result = await WebBrowser.openAuthSessionAsync(pageUrl, returnUrl);
      return result.type === 'success' ? 'returned' : 'closed';
    },
    wait: (ms) =>
      new Promise((resolve) => {
        setTimeout(resolve, ms);
      }),
  };
}

const PaymentsContext = createContext<PaymentsDeps | null>(null);

export function PaymentsProvider({ deps, children }: { deps: PaymentsDeps; children: ReactNode }) {
  return <PaymentsContext value={deps}>{children}</PaymentsContext>;
}

export function usePayments(): PaymentsDeps {
  const deps = useContext(PaymentsContext);
  if (deps === null) throw new Error('usePayments must be used inside PaymentsProvider');
  return deps;
}
