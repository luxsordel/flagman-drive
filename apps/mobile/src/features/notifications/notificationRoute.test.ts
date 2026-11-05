import { notificationRoute } from './notificationRoute';

describe('notificationRoute', () => {
  it('opens the chat for an answer of support, with or without a rental', () => {
    expect(notificationRoute({ event: 'support_reply' })).toBe('/support');
    expect(notificationRoute({ event: 'support_reply', rental_id: 'r-1' })).toBe('/support');
  });

  it('opens the fines for a charged fine', () => {
    expect(notificationRoute({ event: 'fine_charged', rental_id: 'r-1' })).toBe('/fines');
  });

  it.each(['reservation_expiring', 'trip_low_battery'])('opens the rental for %s', (event) => {
    expect(notificationRoute({ event, rental_id: 'r-1' })).toBe('/rental');
  });

  it.each(['reservation_expired', 'trip_finished', 'payment_failed'])(
    'opens the receipt of the rental for %s',
    (event) => {
      expect(notificationRoute({ event, rental_id: 'r-1' })).toEqual({
        pathname: '/rental/receipt',
        params: { id: 'r-1' },
      });
    },
  );

  it('opens the map when the receipt has no rental to load', () => {
    expect(notificationRoute({ event: 'trip_finished' })).toBe('/');
  });

  it.each([null, undefined, 'trip_finished', {}, { event: 'unknown' }, { event: 42 }])(
    'ignores what the server does not send: %p',
    (data) => {
      expect(notificationRoute(data)).toBeNull();
    },
  );
});
