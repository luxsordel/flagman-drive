import type { Href } from 'expo-router';

// The reservation and the trip are on the rental screen.
const routes: Readonly<Record<string, Href>> = {
  reservation_expiring: '/rental',
  // After a restart the app no longer knows the ended reservation: the map is where to go on.
  reservation_expired: '/',
  trip_low_battery: '/rental',
};
// An ended trip and its payment are on the receipt, which loads the rental by its id even after
// a restart.
const toReceipt: ReadonlySet<string> = new Set(['trip_finished', 'payment_failed']);

/** Where a tapped notification leads; null for anything the server does not send (ADR-0020). */
export function notificationRoute(data: unknown): Href | null {
  if (typeof data !== 'object' || data === null) return null;
  const { event, rental_id: rentalId } = data as Record<string, unknown>;
  if (typeof event !== 'string') return null;
  if (toReceipt.has(event)) {
    // Without the rental there is no receipt to show; the map still is.
    return typeof rentalId === 'string' && rentalId !== ''
      ? { pathname: '/rental/receipt', params: { id: rentalId } }
      : '/';
  }
  return Object.hasOwn(routes, event) ? (routes[event] ?? null) : null;
}
