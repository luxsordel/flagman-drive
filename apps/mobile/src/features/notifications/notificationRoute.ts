import type { Href } from 'expo-router';

const events = new Set([
  'reservation_expiring',
  'reservation_expired',
  'trip_low_battery',
  'trip_finished',
  'payment_failed',
]);

/**
 * Where a tapped notification leads; null for anything the server does not send (ADR-0020).
 * Until the reservation, trip and receipt screens exist (#110, #111), every event opens the map,
 * where the client's rental is shown.
 */
export function notificationRoute(data: unknown): Href | null {
  if (typeof data !== 'object' || data === null) return null;
  const event: unknown = (data as Record<string, unknown>)['event'];
  return typeof event === 'string' && events.has(event) ? '/' : null;
}
