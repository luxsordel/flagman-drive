import { describe, expect, it } from 'vitest';

import { demoPrices } from '../test/tariffs';
import { draftOf, emptyDraft, fieldLimit, readDraft } from './form';

describe('tariff form', () => {
  it('turns prices into a draft and back', () => {
    expect(readDraft(draftOf(demoPrices))).toEqual({ prices: demoPrices });
  });

  it('reads rubles with a comma', () => {
    const draft = { ...draftOf(demoPrices), driving_per_minute: '0,45', hold_amount: '50' };

    expect(readDraft(draft)).toEqual({
      prices: { ...demoPrices, driving_per_minute: 45, hold_amount: 5000 },
    });
  });

  it('starts empty with the default free waiting', () => {
    expect(emptyDraft().free_waiting_minutes).toBe('15');
    expect(readDraft(emptyDraft())).toEqual({
      problems: {
        driving_per_minute: 'required',
        parked_per_minute: 'required',
        waiting_per_minute: 'required',
        hold_amount: 'required',
      },
    });
  });

  it('names the problem of each field', () => {
    const draft = {
      driving_per_minute: '1000,01',
      parked_per_minute: 'abc',
      waiting_per_minute: '0,1',
      free_waiting_minutes: '1,5',
      hold_amount: '10001',
    };

    expect(readDraft(draft)).toEqual({
      problems: {
        driving_per_minute: 'tooLarge',
        parked_per_minute: 'format',
        free_waiting_minutes: 'format',
        hold_amount: 'tooLarge',
      },
    });
  });

  it('refuses a zero hold but not free minutes', () => {
    const draft = { ...draftOf(demoPrices), driving_per_minute: '0', hold_amount: '0,00' };

    expect(readDraft(draft)).toEqual({ problems: { hold_amount: 'tooSmall' } });
  });

  it('shows the limits in the units of the field', () => {
    expect(fieldLimit('driving_per_minute')).toBe('1000');
    expect(fieldLimit('hold_amount')).toBe('10000');
    expect(fieldLimit('free_waiting_minutes')).toBe('600');
  });
});
