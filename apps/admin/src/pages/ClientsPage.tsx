import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { loadProblem, type LoadProblem } from '../api/loadProblem';
import { useAuth } from '../auth/context';
import { clientSearchMinLength, type ClientFilter, type ClientSummary } from '../clients/api';
import { fullName } from '../kyc/format';
import { formatMinskTime } from '../time/minsk';

function parseFilter(params: URLSearchParams): ClientFilter {
  const search = params.get('q')?.trim() ?? '';
  const blocked = params.get('blocked');
  return {
    ...(search.length >= clientSearchMinLength ? { search } : {}),
    ...(blocked === 'yes' ? { blocked: true } : {}),
    ...(blocked === 'no' ? { blocked: false } : {}),
  };
}

/** Clients, newest sign-up first, found by a part of the phone or the name. */
export function ClientsPage() {
  const { t, i18n } = useTranslation();
  const { clients } = useApis();
  const { signOut } = useAuth();
  const [params, setParams] = useSearchParams();
  const filter = useMemo(() => parseFilter(params), [params]);
  const [typed, setTyped] = useState(params.get('q') ?? '');
  const [items, setItems] = useState<ClientSummary[]>([]);
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
    clients.list(filter).then(
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
  }, [clients, filter, refused]);

  const setParam = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <>
      <h1>{t('nav.clients')}</h1>
      <form
        className="filters"
        aria-label={t('clients.filters')}
        onSubmit={(event) => {
          event.preventDefault();
          setParam('q', typed.trim());
        }}
      >
        <label className="stacked-label">
          {t('clients.search')}
          <input
            className="field"
            type="search"
            value={typed}
            onChange={(event) => {
              setTyped(event.target.value);
            }}
          />
        </label>
        <label className="stacked-label">
          {t('clients.blocked')}
          <select
            className="field"
            value={params.get('blocked') ?? ''}
            onChange={(event) => {
              setParam('blocked', event.target.value);
            }}
          >
            <option value="">{t('clients.anyone')}</option>
            <option value="yes">{t('clients.onlyBlocked')}</option>
            <option value="no">{t('clients.notBlocked')}</option>
          </select>
        </label>
        <button type="submit" className="button">
          {t('clients.find')}
        </button>
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
      {status === 'ready' && items.length === 0 && <p className="muted">{t('clients.empty')}</p>}
      {status === 'ready' && items.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t('support.columns.client')}</th>
                <th scope="col">{t('clients.phone')}</th>
                <th scope="col">{t('clients.signedUp')}</th>
                <th scope="col">{t('clients.blocked')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={`/clients/${item.id}`}>{fullName(item) ?? t('support.noName')}</Link>
                  </td>
                  <td>{item.phone}</td>
                  <td>
                    <time dateTime={item.created_at}>
                      {formatMinskTime(item.created_at, i18n.language)}
                    </time>
                  </td>
                  <td>{item.blocked ? t('clients.yes') : ''}</td>
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
            clients
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
          {t('clients.more')}
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
