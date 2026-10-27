import type { components } from '@flagman/api-client';

import type { ApiClient } from '../auth/session/authSession';
import { paymentsErrorFromResponse, type PaymentsError } from './paymentsErrors';

export type PaymentCard = components['schemas']['PaymentCardOut'];
export type CardBinding = components['schemas']['CardBindingOut'];
export type CardBindingStarted = components['schemas']['CardBindingStarted'];

export type Result<T> = { ok: true; value: T } | { ok: false; error: PaymentsError };

/** Failures come back as `{ ok: false }`, like ProfileApi: the screen explains each one. */
export interface PaymentsApi {
  listCards(): Promise<Result<PaymentCard[]>>;
  /** `returnUrl` is where the provider's page sends the client back. */
  startBinding(returnUrl: string): Promise<Result<CardBindingStarted>>;
  getBinding(bindingId: string): Promise<Result<CardBinding>>;
  removeCard(cardId: string): Promise<Result<null>>;
  setDefaultCard(cardId: string): Promise<Result<PaymentCard>>;
}

interface Sent<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** `api` must already send the access token. */
export function createPaymentsApi(api: ApiClient): PaymentsApi {
  return {
    listCards: async () => {
      const result = await send(api.GET('/payment-cards'));
      return result.ok ? { ok: true, value: result.value.items } : result;
    },
    startBinding: (returnUrl) =>
      send(api.POST('/payment-cards/bindings', { body: { return_url: returnUrl } })),
    getBinding: (bindingId) =>
      send(
        api.GET('/payment-cards/bindings/{binding_id}', {
          params: { path: { binding_id: bindingId } },
        }),
      ),
    removeCard: async (cardId) => {
      try {
        const { response, error } = await api.DELETE('/payment-cards/{card_id}', {
          params: { path: { card_id: cardId } },
        });
        return response.ok
          ? { ok: true, value: null }
          : { ok: false, error: paymentsErrorFromResponse(response, error) };
      } catch {
        return { ok: false, error: { kind: 'network' } };
      }
    },
    setDefaultCard: (cardId) =>
      send(api.POST('/payment-cards/{card_id}/default', { params: { path: { card_id: cardId } } })),
  };
}

async function send<T>(request: Promise<Sent<T>>): Promise<Result<T>> {
  try {
    const { data, error, response } = await request;
    if (data !== undefined) return { ok: true, value: data };
    return { ok: false, error: paymentsErrorFromResponse(response, error) };
  } catch {
    return { ok: false, error: { kind: 'network' } };
  }
}
