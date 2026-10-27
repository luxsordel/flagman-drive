// Building a formatter is costly and the card formats prices on each render: one per language.
const priceFormats = new Map<string, Intl.NumberFormat>();

/** Kopecks of BYN as rubles of the app's language: "0,39 BYN". The server counts the totals. */
export function formatKopecks(kopecks: number, language: string): string {
  let format = priceFormats.get(language);
  if (!format) {
    format = new Intl.NumberFormat(language, {
      style: 'currency',
      currency: 'BYN',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    priceFormats.set(language, format);
  }
  return format.format(kopecks / 100);
}

/** How far a car is, as the card shows it: meters nearby, kilometers with one decimal beyond. */
export type Distance = { unit: 'meters'; value: string } | { unit: 'kilometers'; value: string };

const metersStep = 10;
const kilometer = 1000;

export function formatDistance(meters: number, language: string): Distance {
  // Steps of 10 m: GPS is not more precise, and a number jumping by 1 m looks broken.
  const rounded = Math.round(meters / metersStep) * metersStep;
  if (rounded < kilometer) return { unit: 'meters', value: String(rounded) };
  const km = new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(
    meters / kilometer,
  );
  return { unit: 'kilometers', value: km };
}
