import { fakeApi, json, problem } from '../auth/session/testing';
import { createPaymentsApi } from './paymentsApi';
import { binding, ok, paymentCard, started } from './testing';

const returnUrl = 'flagmandrive://payment-methods';

function setup() {
  const http = fakeApi();
  return { http, payments: createPaymentsApi(http.client) };
}

describe('createPaymentsApi', () => {
  it('lists the cards', async () => {
    const { http, payments } = setup();
    http.on('/payment-cards', json({ items: [paymentCard()] }));

    await expect(payments.listCards()).resolves.toEqual(ok([paymentCard()]));
  });

  it('starts a binding with the way back to the app', async () => {
    const { http, payments } = setup();
    http.on('/payment-cards/bindings', json(started, 201));

    await expect(payments.startBinding(returnUrl)).resolves.toEqual(ok(started));
    expect(http.calls).toEqual([
      { path: '/payment-cards/bindings', body: { return_url: returnUrl } },
    ]);
  });

  it('reads a binding', async () => {
    const { http, payments } = setup();
    http.on('/payment-cards/bindings/binding-1', json(binding('pending')));

    await expect(payments.getBinding('binding-1')).resolves.toEqual(ok(binding('pending')));
  });

  it('removes a card and makes another the default', async () => {
    const { http, payments } = setup();
    http.on('/payment-cards/card-1', new Response(null, { status: 204 }));
    http.on('/payment-cards/card-2/default', json(paymentCard({ id: 'card-2' })));

    await expect(payments.removeCard('card-1')).resolves.toEqual(ok(null));
    await expect(payments.setDefaultCard('card-2')).resolves.toEqual(
      ok(paymentCard({ id: 'card-2' })),
    );
  });

  it.each([
    ['payment-card-not-found', 404, { kind: 'notFound' }],
    ['payments-unavailable', 503, { kind: 'unavailable' }],
    ['access-token-invalid', 401, { kind: 'sessionExpired' }],
  ])('explains %s', async (name, status, error) => {
    const { http, payments } = setup();
    http.on('/payment-cards/card-1', problem(name, status));

    await expect(payments.removeCard('card-1')).resolves.toEqual({ ok: false, error });
  });

  it('reads how long to wait after too many bindings', async () => {
    const { http, payments } = setup();
    http.on('/payment-cards/bindings', problem('rate-limited', 429, { 'Retry-After': '120' }));

    await expect(payments.startBinding(returnUrl)).resolves.toEqual({
      ok: false,
      error: { kind: 'rateLimited', retryAfterSeconds: 120 },
    });
  });

  it('reports a network error when the API cannot be reached', async () => {
    const { http, payments } = setup();
    http.on('/payment-cards', new TypeError('Network request failed'));
    http.on('/payment-cards/card-1', new TypeError('Network request failed'));

    await expect(payments.listCards()).resolves.toEqual({
      ok: false,
      error: { kind: 'network' },
    });
    await expect(payments.removeCard('card-1')).resolves.toEqual({
      ok: false,
      error: { kind: 'network' },
    });
  });
});
