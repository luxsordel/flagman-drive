import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { useApis } from '../api/context';
import { formatKopecks } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';
import { activeRentalStatuses, type StaffRentalItem } from './api';

/** The page of the newest rentals is enough here; the list itself has the rest and the filters. */
const recentCount = 5;

/** A client's latest rentals for the client card, with a link to all of them. */
export function ClientRentals({ clientId }: { clientId: string }) {
  const { t, i18n } = useTranslation();
  const { rentals } = useApis();
  // Kept with its client: another client's card must not show the rentals of the previous one.
  const [found, setFound] = useState<{
    clientId: string;
    items: StaffRentalItem[] | 'failed';
  } | null>(null);

  useEffect(() => {
    let active = true;
    rentals.list({ userId: clientId }).then(
      (page) => {
        if (active) setFound({ clientId, items: page.items.slice(0, recentCount) });
      },
      () => {
        if (active) setFound({ clientId, items: 'failed' });
      },
    );
    return () => {
      active = false;
    };
  }, [rentals, clientId]);

  const items = found?.clientId === clientId ? found.items : null;
  return (
    <section>
      <h2>{t('clients.rentals')}</h2>
      {items === null && (
        <p role="status" className="muted">
          {t('clients.rentalsLoading')}
        </p>
      )}
      {items === 'failed' && <p className="muted">{t('clients.rentalsFailed')}</p>}
      {Array.isArray(items) && items.length === 0 && (
        <p className="muted">{t('clients.noRentals')}</p>
      )}
      {Array.isArray(items) && items.length > 0 && (
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              <Link to={`/rentals/${item.id}`}>
                {t('clients.rentalLine', {
                  time: formatMinskTime(item.reserved_at, i18n.language),
                  plate: item.car.plate,
                  status: t(`support.rentalStatus.${item.status}`),
                  total: activeRentalStatuses.includes(item.status)
                    ? t('rentals.ongoing')
                    : formatKopecks(item.total, i18n.language),
                })}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p>
        <Link to={`/rentals?client=${clientId}`}>{t('clients.allRentals')}</Link>
      </p>
    </section>
  );
}
