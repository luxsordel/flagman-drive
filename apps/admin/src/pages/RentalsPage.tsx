import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { isCalendarDay } from '../audit/minskTime';
import { loadProblem, type LoadProblem } from '../api/loadProblem';
import { useAuth } from '../auth/context';
import { fullName } from '../kyc/format';
import {
  activeRentalStatuses,
  rentalStatuses,
  type RentalFilter,
  type RentalStatus,
  type StaffRentalItem,
} from '../rentals/api';
import { formatKopecks } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';

type StatusChoice = RentalStatus | 'active' | '';

function parseFilter(params: URLSearchParams): RentalFilter & { status: StatusChoice } {
  const status = params.get('status') ?? '';
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  const choice: StatusChoice =
    status === 'active' || rentalStatuses.includes(status as RentalStatus)
      ? (status as StatusChoice)
      : '';
  return {
    status: choice,
    ...(choice === 'active' ? { statuses: activeRentalStatuses } : {}),
    ...(choice && choice !== 'active' ? { statuses: [choice] } : {}),
    ...(params.get('client') ? { userId: params.get('client') ?? '' } : {}),
    ...(params.get('car') ? { carId: params.get('car') ?? '' } : {}),
    ...(isCalendarDay(from) ? { from } : {}),
    ...(isCalendarDay(to) ? { to } : {}),
  };
}

/** Every client's rentals with filters kept in the address, so a link can open a client's. */
export function RentalsPage() {
  const { t, i18n } = useTranslation();
  const { rentals } = useApis();
  const { signOut } = useAuth();
  const [params, setParams] = useSearchParams();
  const filter = useMemo(() => parseFilter(params), [params]);
  const [items, setItems] = useState<StaffRentalItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | LoadProblem>('loading');
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  // Counts the filters shown: a next page asked for under an older one is dropped.
  const shown = useRef(0);

  const refused = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setStatus(loadProblem(error));
    },
    [signOut],
  );

  useEffect(() => {
    // An answer to the filter before is dropped: it would replace the list of this one.
    let active = true;
    shown.current += 1;
    rentals.list(filter).then(
      (page) => {
        if (!active) return;
        setItems(page.items);
        setCursor(page.next_cursor ?? null);
        setStatus('ready');
      },
      (error: unknown) => {
        if (active) refused(error);
      },
    );
    return () => {
      active = false;
    };
  }, [rentals, filter, refused]);

  const setParam = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <>
      <h1>{t('nav.rentals')}</h1>
      <form
        className="filters"
        aria-label={t('rentals.filters')}
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label className="stacked-label">
          {t('rentals.status')}
          <select
            className="field"
            value={filter.status}
            onChange={(event) => {
              setParam('status', event.target.value);
            }}
          >
            <option value="">{t('rentals.anyStatus')}</option>
            <option value="active">{t('rentals.active')}</option>
            {rentalStatuses.map((value) => (
              <option key={value} value={value}>
                {t(`support.rentalStatus.${value}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="stacked-label">
          {t('rentals.from')}
          <input
            className="field"
            type="date"
            max={filter.to}
            value={filter.from ?? ''}
            onChange={(event) => {
              setParam('from', event.target.value);
            }}
          />
        </label>
        <label className="stacked-label">
          {t('rentals.to')}
          <input
            className="field"
            type="date"
            min={filter.from}
            value={filter.to ?? ''}
            onChange={(event) => {
              setParam('to', event.target.value);
            }}
          />
        </label>
        {(filter.userId ?? filter.carId) && (
          <button
            type="button"
            className="button"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete('client');
              next.delete('car');
              setParams(next);
            }}
          >
            {filter.userId ? t('rentals.oneClient') : t('rentals.oneCar')} · {t('rentals.showAll')}
          </button>
        )}
      </form>
      {status === 'loading' && (
        <p role="status" className="muted">
          {t('support.loading')}
        </p>
      )}
      {status !== 'loading' && status !== 'ready' && (
        <p role="alert" className="list-error">
          {t(`load.${status}`)}
        </p>
      )}
      {status === 'ready' && items.length === 0 && <p className="muted">{t('rentals.empty')}</p>}
      {status === 'ready' && items.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t('rentals.reserved')}</th>
                <th scope="col">{t('rentals.status')}</th>
                <th scope="col">{t('support.columns.client')}</th>
                <th scope="col">{t('support.columns.car')}</th>
                <th scope="col">{t('rentals.total')}</th>
                <th scope="col">{t('rentals.payment')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={`/rentals/${item.id}`}>
                      <time dateTime={item.reserved_at}>
                        {formatMinskTime(item.reserved_at, i18n.language)}
                      </time>
                    </Link>
                  </td>
                  <td>{t(`support.rentalStatus.${item.status}`)}</td>
                  <td>
                    <Link to={`/clients/${item.client.id}`}>
                      {fullName(item.client) ?? t('support.noName')}
                    </Link>
                  </td>
                  <td>{item.car.plate}</td>
                  <td>
                    {/* No receipt yet: a zero here would read as a free trip. */}
                    {activeRentalStatuses.includes(item.status)
                      ? t('rentals.ongoing')
                      : formatKopecks(item.total, i18n.language)}
                  </td>
                  <td>{item.charge_status ? t(`rentals.charge.${item.charge_status}`) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {status === 'ready' && cursor && (
        <button
          type="button"
          className="button list-more"
          disabled={loadingMore}
          onClick={() => {
            const asked = shown.current;
            setLoadingMore(true);
            setMoreFailed(false);
            rentals
              .list(filter, cursor)
              .then(
                (page) => {
                  if (asked !== shown.current) return;
                  setItems((known) => [...known, ...page.items]);
                  setCursor(page.next_cursor ?? null);
                },
                (error: unknown) => {
                  if (asked !== shown.current) return;
                  // The rows loaded so far stay: only the next page failed.
                  if (error instanceof ApiError && error.status === 401) void signOut();
                  else setMoreFailed(true);
                },
              )
              .finally(() => {
                setLoadingMore(false);
              });
          }}
        >
          {t('rentals.more')}
        </button>
      )}
      {status === 'ready' && moreFailed && (
        <p role="alert" className="list-error">
          {t('load.moreFailed')}
        </p>
      )}
    </>
  );
}
