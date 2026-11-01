import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { fullName } from '../kyc/format';
import {
  incidentStatuses,
  incidentTypes,
  type Incident,
  type IncidentFilter,
  type IncidentStatus,
  type IncidentType,
} from '../support/api';
import { listPollMs, useInterval } from '../support/useInterval';
import { formatMinskTime } from '../time/minsk';

function parseFilter(params: URLSearchParams): IncidentFilter {
  const status = params.get('status') as IncidentStatus | null;
  const type = params.get('type') as IncidentType | null;
  return {
    ...(status && incidentStatuses.includes(status) ? { status } : {}),
    ...(type && incidentTypes.includes(type) ? { type } : {}),
  };
}

/** Incidents with filters kept in the address; accidents during a trip come first in the queue. */
export function IncidentsPage() {
  const { t, i18n } = useTranslation();
  const { support } = useApis();
  const { signOut } = useAuth();
  const [params, setParams] = useSearchParams();
  const filter = useMemo(() => parseFilter(params), [params]);
  const [items, setItems] = useState<Incident[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [paged, setPaged] = useState(false);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');

  const refused = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setStatus((previous) => (previous === 'ready' ? previous : 'failed'));
    },
    [signOut],
  );

  const loadFirst = useCallback(
    () =>
      support.incidents(filter).then((page) => {
        setItems(page.items);
        setCursor(page.next_cursor ?? null);
        setPaged(false);
        setStatus('ready');
      }, refused),
    [support, filter, refused],
  );

  useEffect(() => {
    void loadFirst();
  }, [loadFirst]);
  // Not over pages loaded by hand: the operator would lose the place in the list.
  useInterval(() => {
    if (!paged) void loadFirst();
  }, listPollMs);

  const setFilter = (name: keyof IncidentFilter, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <>
      <h1>{t('nav.incidents')}</h1>
      <form
        className="filters"
        aria-label={t('incidents.filters')}
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label className="stacked-label">
          {t('incidents.status')}
          <select
            className="field"
            value={filter.status ?? ''}
            onChange={(event) => {
              setFilter('status', event.target.value);
            }}
          >
            <option value="">{t('incidents.any')}</option>
            {incidentStatuses.map((value) => (
              <option key={value} value={value}>
                {t(`incidents.statuses.${value}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="stacked-label">
          {t('incidents.type')}
          <select
            className="field"
            value={filter.type ?? ''}
            onChange={(event) => {
              setFilter('type', event.target.value);
            }}
          >
            <option value="">{t('incidents.any')}</option>
            {incidentTypes.map((value) => (
              <option key={value} value={value}>
                {t(`incidents.types.${value}`)}
              </option>
            ))}
          </select>
        </label>
      </form>
      {status === 'loading' && (
        <p role="status" className="muted">
          {t('support.loading')}
        </p>
      )}
      {status === 'failed' && (
        <p role="alert" className="list-error">
          {t('support.failed')}
        </p>
      )}
      {status === 'ready' && items.length === 0 && <p className="muted">{t('incidents.empty')}</p>}
      {status === 'ready' && items.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t('incidents.type')}</th>
                <th scope="col">{t('incidents.status')}</th>
                <th scope="col">{t('support.columns.client')}</th>
                <th scope="col">{t('support.columns.car')}</th>
                <th scope="col">{t('support.columns.operator')}</th>
                <th scope="col">{t('incidents.created')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={`/incidents/${item.id}`}>{t(`incidents.types.${item.type}`)}</Link>
                    {item.priority === 0 && <strong> {t('incidents.urgent')}</strong>}
                  </td>
                  <td>{t(`incidents.statuses.${item.status}`)}</td>
                  <td>{fullName(item) ?? t('support.noName')}</td>
                  <td>{item.car_plate ?? ''}</td>
                  <td>{item.assigned_staff_name ?? ''}</td>
                  <td>
                    <time dateTime={item.created_at}>
                      {formatMinskTime(item.created_at, i18n.language)}
                    </time>
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
            support.incidents(filter, cursor).then((page) => {
              setItems((known) => [...known, ...page.items]);
              setCursor(page.next_cursor ?? null);
              setPaged(true);
            }, refused);
          }}
        >
          {t('incidents.more')}
        </button>
      )}
    </>
  );
}
