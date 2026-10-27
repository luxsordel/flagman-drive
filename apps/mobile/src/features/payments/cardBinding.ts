import type { PaymentCard, PaymentsApi } from './paymentsApi';
import type { PaymentsError } from './paymentsErrors';

export type BindingOutcome =
  | { kind: 'succeeded'; card: PaymentCard }
  | { kind: 'declined' }
  | { kind: 'cancelled' }
  | { kind: 'expired' }
  // The client came back, but the provider has not reported yet: the list may show it later.
  | { kind: 'pending' }
  | { kind: 'error'; error: PaymentsError };

/** How the provider's page ended for the app: sent back to `returnUrl`, or closed by the client. */
export type BrowserResult = 'returned' | 'closed';

export interface BindingDeps {
  api: PaymentsApi;
  /** Opens the provider's page and resolves when the client is back in the app. */
  openBrowser: (pageUrl: string, returnUrl: string) => Promise<BrowserResult>;
  returnUrl: string;
  wait: (ms: number) => Promise<void>;
}

// The provider reports by a webhook of its own, which may come a moment after the redirect.
export const bindingPolls = 10;
export const bindingPollIntervalMs = 1000;

/**
 * Binds a card the way docs/adr/0019 describes: the API opens a binding, the client enters the card
 * on the provider's page, and the API is asked for the outcome. The card number never passes
 * through the app.
 */
export async function bindCard({
  api,
  openBrowser,
  returnUrl,
  wait,
}: BindingDeps): Promise<BindingOutcome> {
  const started = await api.startBinding(returnUrl);
  if (!started.ok) return { kind: 'error', error: started.error };
  const browser = await openBrowser(started.value.page_url, returnUrl);

  for (let poll = 0; poll < bindingPolls; poll += 1) {
    if (poll > 0) await wait(bindingPollIntervalMs);
    const binding = await api.getBinding(started.value.id);
    if (!binding.ok) return { kind: 'error', error: binding.error };
    const { status, card } = binding.value;
    if (status === 'succeeded' && card) return { kind: 'succeeded', card };
    if (status === 'declined' || status === 'cancelled' || status === 'expired') {
      return { kind: status };
    }
    // Closed before the page sent the client back: nothing was entered, nothing will come.
    if (browser === 'closed') return { kind: 'cancelled' };
  }
  return { kind: 'pending' };
}
