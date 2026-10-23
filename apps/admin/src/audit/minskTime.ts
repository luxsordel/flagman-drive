/** The pilot runs in Minsk: staff read and filter the log in its local time, whatever their own zone. */
export const minskTimeZone = 'Europe/Minsk';

const offsetFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: minskTimeZone,
  timeZoneName: 'longOffset',
});

const calendarDayShape = /^\d{4}-\d{2}-\d{2}$/;

/**
 * A real calendar day as `<input type="date">` gives it: `YYYY-MM-DD`. The round trip rejects
 * days that only look right, such as `2026-02-30`, which Date would roll over or fail on.
 */
export function isCalendarDay(value: string): boolean {
  if (!calendarDayShape.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(value);
}

/** The moment a Minsk calendar day starts, with Minsk's offset on that day (`…T00:00:00+03:00`). */
export function minskDayStart(day: string): string {
  // Noon UTC falls on the same calendar day in Minsk, so its offset is the one of that day.
  const noon = new Date(`${day}T12:00:00Z`);
  const zoneName = offsetFormat.formatToParts(noon).find((part) => part.type === 'timeZoneName');
  const offset = zoneName && zoneName.value !== 'GMT' ? zoneName.value.slice('GMT'.length) : 'Z';
  return `${day}T00:00:00${offset}`;
}

export function nextDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 'YYYY-MM-DD'.length);
}

// Building a formatter is costly and the log formats every row on each render: one per language.
const timeFormats = new Map<string, Intl.DateTimeFormat>();

export function formatMinskTime(iso: string, language: string): string {
  let format = timeFormats.get(language);
  if (!format) {
    format = new Intl.DateTimeFormat(language, {
      timeZone: minskTimeZone,
      dateStyle: 'short',
      timeStyle: 'medium',
    });
    timeFormats.set(language, format);
  }
  return format.format(new Date(iso));
}
