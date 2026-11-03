import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useApis } from '../api/context';
import { activeRentalStatuses } from './api';

/**
 * The car's rental right now, for the fleet card; looked up only when the car is rented out.
 * A failed lookup shows nothing: the card is about the car, the rental has its own page.
 */
export function CurrentRentalLink({ carId }: { carId: string }) {
  const { t } = useTranslation();
  const { rentals } = useApis();
  // Kept with its car: another car's card must not show the link of the previous one.
  const [found, setFound] = useState<{ carId: string; rentalId: string | null } | null>(null);

  useEffect(() => {
    let current = true;
    rentals.list({ carId, statuses: activeRentalStatuses }).then(
      (page) => {
        if (current) setFound({ carId, rentalId: page.items[0]?.id ?? null });
      },
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [rentals, carId]);

  if (found?.carId !== carId || !found.rentalId) return null;
  return (
    <p>
      <Link to={`/rentals/${found.rentalId}`}>{t('rentals.current')}</Link>
    </p>
  );
}
