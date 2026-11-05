import { fakeApi, json } from '../auth/session/testing';
import { createRentalApi } from './rentalApi';
import { fine } from './testing';

function setup() {
  const http = fakeApi();
  return {
    http,
    rentals: createRentalApi({
      api: http.client,
      baseUrl: 'https://api.test/',
      getAccessToken: () => Promise.resolve('token'),
    }),
  };
}

describe('createRentalApi', () => {
  it('reads every page of the fines', async () => {
    const { http, rentals } = setup();
    http.on(
      '/support/fines',
      json({ items: [fine({ id: 'f-1' })], next_cursor: 'c-1' }),
      json({ items: [fine({ id: 'f-2' })], next_cursor: null }),
    );

    const result = await rentals.fines();

    expect(result.ok && result.value.map((item) => item.id)).toEqual(['f-1', 'f-2']);
    expect(http.calls).toHaveLength(2);
  });

  it('gives up on a page that fails', async () => {
    const { http, rentals } = setup();
    http.on('/support/fines', json({ items: [fine()], next_cursor: 'c-1' }), new Error('offline'));

    const result = await rentals.fines();

    expect(result).toEqual({ ok: false, error: { kind: 'network' } });
  });
});
