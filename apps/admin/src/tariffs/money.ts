/** Prices travel as integer kopecks of BYN (docs/02-architecture.md); staff read and type rubles. */

// Building a formatter is costly and the table formats every price on each render: one per language.
const formats = new Map<string, Intl.NumberFormat>();

export function formatKopecks(kopecks: number, language: string): string {
  let format = formats.get(language);
  if (!format) {
    format = new Intl.NumberFormat(language, {
      style: 'currency',
      currency: 'BYN',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    formats.set(language, format);
  }
  return format.format(kopecks / 100);
}

/** Rubles as a staff member types them: "0,39", "0.39", "12" or "1 200,50"; \s covers NBSP too. */
const rublesPattern = /^(\d{1,7})(?:[.,](\d{1,2}))?$/;

/**
 * Kopecks of a typed amount of rubles, or null if it is not one. Parsed as text, not as a float:
 * 0.29 * 100 is 28.999999999999996 in floating point.
 */
export function parseRubles(text: string): number | null {
  const match = rublesPattern.exec(text.replace(/\s/g, ''));
  if (!match) return null;
  const [, rubles = '0', fraction = ''] = match;
  return Number(rubles) * 100 + Number(fraction.padEnd(2, '0'));
}

/** Kopecks as the form shows them for editing: "0.39", without the currency. */
export function kopecksToRubles(kopecks: number): string {
  const rubles = Math.floor(kopecks / 100);
  return `${String(rubles)}.${String(kopecks % 100).padStart(2, '0')}`;
}
