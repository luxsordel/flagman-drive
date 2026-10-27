import { notificationRoute } from './notificationRoute';

describe('notificationRoute', () => {
  it.each([
    'reservation_expiring',
    'reservation_expired',
    'trip_low_battery',
    'trip_finished',
    'payment_failed',
  ])('opens the map for %s', (event) => {
    expect(notificationRoute({ event, rental_id: 'r-1' })).toBe('/');
  });

  it.each([null, undefined, 'trip_finished', {}, { event: 'unknown' }, { event: 42 }])(
    'ignores what the server does not send: %p',
    (data) => {
      expect(notificationRoute(data)).toBeNull();
    },
  );
});
