/** The pilot runs in Minsk: staff read times in its local time, whatever their own zone. */
export const minskTimeZone = 'Europe/Minsk';

// Building a formatter is costly and lists format every row on each render: one per language.
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
