import type { BindingDeps, BrowserResult } from './cardBinding';
import type {
  CardBinding,
  CardBindingStarted,
  PaymentCard,
  PaymentsApi,
  Result,
} from './paymentsApi';

/** Test doubles shared by the payments tests. */

export function paymentCard(overrides: Partial<PaymentCard> = {}): PaymentCard {
  return {
    id: 'card-1',
    brand: 'visa',
    last4: '4242',
    exp_month: 12,
    exp_year: 2030,
    is_default: true,
    expired: false,
    ...overrides,
  };
}

export const ok = <T>(value: T): Result<T> => ({ ok: true, value });

export const started: CardBindingStarted = {
  id: 'binding-1',
  page_url: 'http://api.test/fake-provider/cards?session=s',
  expires_at: '2026-10-26T12:30:00Z',
};

export function binding(
  status: CardBinding['status'],
  card: PaymentCard | null = null,
): CardBinding {
  return {
    id: started.id,
    status,
    card,
    decline_reason: status === 'declined' ? 'declined' : null,
  };
}

export function fakePaymentsApi() {
  return {
    listCards: jest.fn<Promise<Result<PaymentCard[]>>, []>(),
    startBinding: jest.fn<Promise<Result<CardBindingStarted>>, [string]>(),
    getBinding: jest.fn<Promise<Result<CardBinding>>, [string]>(),
    removeCard: jest.fn<Promise<Result<null>>, [string]>(),
    setDefaultCard: jest.fn<Promise<Result<PaymentCard>>, [string]>(),
  } satisfies PaymentsApi;
}

export function fakeBindingDeps(browser: BrowserResult = 'returned') {
  const api = fakePaymentsApi();
  return {
    api,
    returnUrl: 'flagmandrive://payment-methods',
    openBrowser: jest.fn<Promise<BrowserResult>, [string, string]>(() => Promise.resolve(browser)),
    wait: jest.fn<Promise<void>, [number]>(() => Promise.resolve()),
  } satisfies BindingDeps;
}
