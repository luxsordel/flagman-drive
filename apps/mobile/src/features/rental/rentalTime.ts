import type { LatLon } from '../map/mapConfig';
import type { Rental } from './rentalApi';

/** Where a reservation stands: the deadlines are the server's, counted on its clock. */
export type ReservationPhase =
  { kind: 'free'; leftMs: number } | { kind: 'paid'; leftMs: number } | { kind: 'over' };

/**
 * How far the server's clock is ahead of the phone's, from `as_of` of an answer and the phone's
 * time when it came. A phone with a wrong clock still counts the reservation down correctly.
 */
export function clockOffsetMs(asOf: string, receivedAtMs: number): number {
  return Date.parse(asOf) - receivedAtMs;
}

export function reservationPhase(rental: Rental, serverNowMs: number): ReservationPhase {
  const freeUntil = Date.parse(rental.free_wait_until);
  const expiresAt = Date.parse(rental.expires_at);
  if (serverNowMs < freeUntil) return { kind: 'free', leftMs: freeUntil - serverNowMs };
  if (serverNowMs < expiresAt) return { kind: 'paid', leftMs: expiresAt - serverNowMs };
  return { kind: 'over' };
}

/** `m:ss`, or `h:mm:ss` from an hour on; a started second counts as a whole one. */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, '0');
  return hours > 0
    ? `${String(hours)}:${String(minutes).padStart(2, '0')}:${seconds}`
    : `${String(minutes)}:${seconds}`;
}

const earthRadiusM = 6_371_000;
const radians = (degrees: number) => (degrees * Math.PI) / 180;

/**
 * Meters between two points on a sphere: good to a few meters at city scale, enough to show the
 * distance. Whether the car may be opened is decided by the server on its own measure.
 */
export function distanceMeters(a: LatLon, b: LatLon): number {
  const dLat = radians(b.lat - a.lat);
  const dLon = radians(b.lon - a.lon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(radians(a.lat)) * Math.cos(radians(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * earthRadiusM * Math.asin(Math.sqrt(h));
}

export const activeStatuses: readonly Rental['status'][] = ['reserved', 'in_trip', 'parked'];

export function isActive(rental: Rental): boolean {
  return activeStatuses.includes(rental.status);
}

export function isOnTrip(rental: Rental): boolean {
  return rental.status === 'in_trip' || rental.status === 'parked';
}

/** How long the trip has lasted by the server's clock; zero before it starts. */
export function tripElapsedMs(rental: Rental, serverNowMs: number): number {
  if (rental.trip_started_at === null) return 0;
  const end = rental.ended_at === null ? serverNowMs : Date.parse(rental.ended_at);
  return Math.max(0, end - Date.parse(rental.trip_started_at));
}
