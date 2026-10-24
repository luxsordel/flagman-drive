import type { KycDocumentKind } from './api';

/** The order operators check the documents in: the license first, the selfie against it last. */
export const documentKinds: readonly KycDocumentKind[] = [
  'license_front',
  'license_back',
  'passport',
  'selfie_with_license',
];

/** Same limit as REJECTION_REASON_MAX_LENGTH in the API. */
export const rejectionReasonMaxLength = 500;

/** `Last First`, as on the documents; null when the client has not filled in either. */
export function fullName(person: { first_name: string | null; last_name: string | null }) {
  const name = [person.last_name, person.first_name].filter(Boolean).join(' ');
  return name === '' ? null : name;
}

/**
 * Length as the API counts it, in code points after trimming. String.length counts UTF-16 units
 * and would turn away a reason with emoji or rare letters that the API accepts.
 */
export function reasonLength(reason: string): number {
  return Array.from(reason.trim()).length;
}

/**
 * A `YYYY-MM-DD` date as it is, without a time zone moving it to the day before. The month by name
 * and the full year: the operator compares it with a passport, and `5/17/90` reads both ways.
 */
export function formatCalendarDay(day: string, language: string): string {
  return new Intl.DateTimeFormat(language, { timeZone: 'UTC', dateStyle: 'medium' }).format(
    new Date(`${day}T00:00:00Z`),
  );
}

const minute = 60_000;
const hour = 60 * minute;
const day = 24 * hour;

/** How long an application has waited, in the largest unit that keeps it readable: `3 hours ago`. */
export function formatWaiting(submittedAt: string, now: Date, language: string): string {
  const waited = Math.max(0, now.getTime() - new Date(submittedAt).getTime());
  const format = new Intl.RelativeTimeFormat(language, { numeric: 'always', style: 'long' });
  if (waited < hour) return format.format(-Math.floor(waited / minute), 'minute');
  if (waited < 2 * day) return format.format(-Math.floor(waited / hour), 'hour');
  return format.format(-Math.floor(waited / day), 'day');
}
