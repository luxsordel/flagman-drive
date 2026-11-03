import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { loadProblem, type LoadProblem } from '../api/loadProblem';
import { isCalendarDay } from '../audit/minskTime';
import { useAuth, useStaff } from '../auth/context';
import {
  fineStatuses,
  type Fine,
  type FineFilter,
  type FineStatus,
  type FineType,
} from '../fines/api';
import { FineCatalog } from '../fines/FineCatalog';
import { fineName } from '../fines/format';
import { fullName } from '../kyc/format';
import { formatKopecks } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';

const uuidShape = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function parseFilter(params: URLSearchParams): FineFilter {
  const status = params.get('status') as FineStatus | null;
  const type = params.get('type') ?? '';
  const client = params.get('client') ?? '';
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  return {
    ...(status && fineStatuses.includes(status) ? { status } : {}),
    ...(uuidShape.test(type) ? { fineTypeId: type } : {}),
    ...(uuidShape.test(client) ? { userId: client } : {}),
    ...(isCalendarDay(from) ? { from } : {}),
    ...(isCalendarDay(to) ? { to } : {}),
  };
}

/** Charged fines with filters kept in the address, and the catalog they are charged by. */
export function FinesPage() {
  const { t, i18n } = useTranslation();
  const { fines } = useApis();
  const { signOut } = useAuth();
  const canEdit = useStaff().role === 'admin';
  const [params, setParams] = useSearchParams();
  const filter = useMemo(() => parseFilter(params), [params]);
  const [types, setTypes] = useState<FineType[]>([]);
  const [items, setItems] = useState<Fine[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [problem, setProblem] = useState<LoadProblem | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready'>('loading');

  const refused = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setProblem(loadProblem(error));
    },
    [signOut],
  );

  const loadTypes = useCallback(() => {
    fines.types().then(setTypes, refused);
  }, [fines, refused]);

  useEffect(() => {
    loadTypes();
  }, [loadTypes]);

  useEffect(() => {
    // An answer to the filter before is dropped: it would replace the list of this one.
    let active = true;
    fines.list(filter).then(
      (page) => {
        if (!active) return;
        setItems(page.items);
        setCursor(page.next_cursor ?? null);
        setProblem(null);
        setStatus('ready');
      },
      (error: unknown) => {
        if (active) refused(error);
      },
    );
    return () => {
      active = false;
    };
  }, [fines, filter, refused]);

  const setParam = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <>
      <h1>{t('nav.fines')}</h1>
      <form
        className="filters"
        aria-label={t('fines.filters')}
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label className="stacked-label">
          {t('fines.status')}
          <select
            className="field"
            value={filter.status ?? ''}
            onChange={(event) => {
              setParam('status', event.target.value);
            }}
          >
            <option value="">{t('fines.any')}</option>
            {fineStatuses.map((value) => (
              <option key={value} value={value}>
                {t(`fines.statuses.${value}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="stacked-label">
          {t('fines.type')}
          <select
            className="field"
            value={filter.fineTypeId ?? ''}
            onChange={(event) => {
              setParam('type', event.target.value);
            }}
          >
            <option value="">{t('fines.any')}</option>
            {types.map((type) => (
              <option key={type.fine_type_id} value={type.fine_type_id}>
                {fineName(type, i18n.language)}
              </option>
            ))}
          </select>
        </label>
        <label className="stacked-label">
          {t('fines.from')}
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
          {t('fines.to')}
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
        {filter.userId && (
          <p>
            {t('fines.oneClient')}{' '}
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setParam('client', '');
              }}
            >
              {t('fines.showAll')}
            </button>
          </p>
        )}
      </form>
      {problem && (
        <p role="alert" className="list-error">
          {t(`load.${problem}`)}
        </p>
      )}
      {!problem && status === 'loading' && (
        <p role="status" className="muted">
          {t('support.loading')}
        </p>
      )}
      {status === 'ready' && items.length === 0 && <p className="muted">{t('fines.empty')}</p>}
      {status === 'ready' && items.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t('fines.created')}</th>
                <th scope="col">{t('fines.type')}</th>
                <th scope="col">{t('fines.amount')}</th>
                <th scope="col">{t('fines.status')}</th>
                <th scope="col">{t('support.columns.client')}</th>
                <th scope="col">{t('fines.rental')}</th>
                <th scope="col">{t('fines.by')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <time dateTime={item.created_at}>
                      {formatMinskTime(item.created_at, i18n.language)}
                    </time>
                  </td>
                  <td>{fineName(item, i18n.language)}</td>
                  <td>{formatKopecks(item.amount, i18n.language)}</td>
                  <td>{t(`fines.statuses.${item.status}`)}</td>
                  <td>
                    <Link to={`/fines?client=${item.user_id}`}>
                      {fullName(item) ?? t('support.noName')}
                    </Link>
                  </td>
                  <td>
                    <Link to={`/rentals/${item.rental_id}`}>
                      {item.car_plate ?? t('fines.open')}
                    </Link>
                  </td>
                  <td>
                    {item.automatic
                      ? t('fines.automatic')
                      : (item.created_by_name ?? t('support.nobody'))}
                  </td>
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
          onClick={() => {
            fines.list(filter, cursor).then((page) => {
              setItems((known) => [...known, ...page.items]);
              setCursor(page.next_cursor ?? null);
            }, refused);
          }}
        >
          {t('fines.more')}
        </button>
      )}
      <FineCatalog types={types} canEdit={canEdit} onChanged={loadTypes} />
    </>
  );
}
