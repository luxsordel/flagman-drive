import type { Href } from 'expo-router';

// The trip and payment events open their screens with #111; until then the map, which shows the
// client's rental.
const routes: Readonly<Record<string, Href>> = {
  reservation_expiring: '/rental',
  // After a restart the app no longer knows the ended reservation: the map is where to go on.
  reservation_expired: '/',
  trip_low_battery: '/',
  trip_finished: '/',
  payment_failed: '/',
};

/** Where a tapped notification leads; null for anything the server does not send (ADR-0020). */
export function notificationRoute(data: unknown): Href | null {
  if (typeof data !== 'object' || data === null) return null;
  const event: unknown = (data as Record<string, unknown>)['event'];
  return typeof event === 'string' && Object.hasOwn(routes, event) ? (routes[event] ?? null) : null;
}
