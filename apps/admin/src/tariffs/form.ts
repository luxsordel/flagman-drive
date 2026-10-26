import type { TariffPrices } from './api';
import { kopecksToRubles, parseRubles } from './money';

/** The fields of the form, in the order it shows them. Keys under `tariffs.fields`. */
export const priceFields = [
  'driving_per_minute',
  'parked_per_minute',
  'waiting_per_minute',
  'free_waiting_minutes',
  'hold_amount',
] as const;

export type PriceField = (typeof priceFields)[number];

/** As typed: rubles for amounts, whole minutes for the free waiting. */
export type TariffDraft = Record<PriceField, string>;

// The API's typo guards (pricing/domain.py): a lost or extra digit, not a business rule.
const limits: Record<PriceField, number> = {
  driving_per_minute: 100_000,
  parked_per_minute: 100_000,
  waiting_per_minute: 100_000,
  free_waiting_minutes: 600,
  hold_amount: 1_000_000,
};

export const defaultFreeWaitingMinutes = 15;

export function emptyDraft(): TariffDraft {
  return {
    driving_per_minute: '',
    parked_per_minute: '',
    waiting_per_minute: '',
    free_waiting_minutes: String(defaultFreeWaitingMinutes),
    hold_amount: '',
  };
}

export function draftOf(prices: TariffPrices): TariffDraft {
  return {
    driving_per_minute: kopecksToRubles(prices.driving_per_minute),
    parked_per_minute: kopecksToRubles(prices.parked_per_minute),
    waiting_per_minute: kopecksToRubles(prices.waiting_per_minute),
    free_waiting_minutes: String(prices.free_waiting_minutes),
    hold_amount: kopecksToRubles(prices.hold_amount),
  };
}

export type FieldProblem = 'required' | 'format' | 'tooLarge';

function parseField(field: PriceField, text: string): number | FieldProblem {
  if (text.trim() === '') return 'required';
  const value =
    field === 'free_waiting_minutes'
      ? /^\d{1,4}$/.test(text.trim())
        ? Number(text.trim())
        : null
      : parseRubles(text);
  if (value === null) return 'format';
  return value > limits[field] ? 'tooLarge' : value;
}

/** The prices of a draft in kopecks, or the problem of each field that has one. */
export function readDraft(
  draft: TariffDraft,
): { prices: TariffPrices } | { problems: Partial<Record<PriceField, FieldProblem>> } {
  const problems: Partial<Record<PriceField, FieldProblem>> = {};
  const values: Partial<Record<PriceField, number>> = {};
  for (const field of priceFields) {
    const parsed = parseField(field, draft[field]);
    if (typeof parsed === 'number') values[field] = parsed;
    else problems[field] = parsed;
  }
  if (Object.keys(problems).length > 0) return { problems };
  return {
    prices: {
      driving_per_minute: values.driving_per_minute ?? 0,
      parked_per_minute: values.parked_per_minute ?? 0,
      waiting_per_minute: values.waiting_per_minute ?? 0,
      free_waiting_minutes: values.free_waiting_minutes ?? 0,
      hold_amount: values.hold_amount ?? 0,
    },
  };
}

/** The maximum of a field as the form shows it: rubles for amounts, minutes for the free waiting. */
export function fieldLimit(field: PriceField): string {
  return field === 'free_waiting_minutes' ? String(limits[field]) : String(limits[field] / 100);
}
