import type { Rental } from './rentalApi';

type EndedKey = 'rental.ended.expired' | 'rental.ended.client' | 'rental.ended.other';

/** Why the rental is over, as the client is told. */
export function endedText(rental: Rental): EndedKey {
  if (rental.cancel_reason === 'expired') return 'rental.ended.expired';
  if (rental.cancel_reason === 'client') return 'rental.ended.client';
  return 'rental.ended.other';
}
