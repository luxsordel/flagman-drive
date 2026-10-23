import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { actionLabelKey, auditActions, isAuditAction } from '../audit/actions';
import type { AuditEvent, AuditLogFilter } from '../audit/api';
import { firstLogDay, formatMinskTime, isCalendarDay } from '../audit/minskTime';
import { useAuth } from '../auth/context';

// Filters live in the address, so a filtered log can be reloaded or sent to a colleague.
const filterParams = { actorId: 'staff', action: 'action', from: 'from', to: 'to' } as const;
type FilterKey = keyof typeof filterParams;

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Days before the log began can only narrow it to nothing, or break the request (see firstLogDay).
const isLogDay = (value: string): boolean => isCalendarDay(value) && value >= firstLogDay;

// Malformed values from a hand-edited address are dropped rather than sent to the API.
function parseFilter(params: URLSearchParams): AuditLogFilter {
  const actorId = params.get(filterParams.actorId);
  const action = params.get(filterParams.action);
  const from = params.get(filterParams.from);
  const to = params.get(filterParams.to);
  return {
    ...(actorId && uuid.test(actorId) ? { actorId } : {}),
    ...(action && isAuditAction(action) ? { action } : {}),
    ...(from && isLogDay(from) ? { from } : {}),
    ...(to && isLogDay(to) ? { to } : {}),
  };
}

/** Enough to tell staff apart in a list until the log shows who they are (#56). */
const shortId = (id: string): string => id.slice(0, 8);

/** Admin-only list of audit events, newest first, with filters and page-by-page loading. */
export function AuditLogPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const filter = useMemo(() => parseFilter(params), [params]);

  const setFilter = (key: FilterKey, value: string | undefined): void => {
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(filterParams[key], value);
        else next.delete(filterParams[key]);
        return next;
      },
      { replace: true },
    );
  };

  const hasFilters = Object.keys(filter).length > 0;
  // `min` and `max` do not stop a typed or linked range: the API would answer an empty list.
  const invertedRange =
    filter.from !== undefined && filter.to !== undefined && filter.from > filter.to;

  return (
    <>
      <h1>{t('nav.audit')}</h1>
      <form
        className="filters"
        aria-label={t('audit.filters')}
        onSubmit={(event) => {
          event.preventDefault();
        }}
      >
        <label className="stacked-label">
          {t('audit.action')}
          <select
            className="field"
            value={filter.action ?? ''}
            onChange={(event) => {
              setFilter('action', event.target.value);
            }}
          >
            <option value="">{t('audit.anyAction')}</option>
            {auditActions.map((action) => (
              <option key={action} value={action}>
                {t(actionLabelKey(action))}
              </option>
            ))}
          </select>
        </label>
        <label className="stacked-label">
          {t('audit.from')}
          <input
            className="field"
            type="date"
            value={filter.from ?? ''}
            min={firstLogDay}
            max={filter.to}
            onChange={(event) => {
              setFilter('from', event.target.value);
            }}
          />
        </label>
        <label className="stacked-label">
          {t('audit.to')}
          <input
            className="field"
            type="date"
            value={filter.to ?? ''}
            min={filter.from ?? firstLogDay}
            onChange={(event) => {
              setFilter('to', event.target.value);
            }}
          />
        </label>
        {filter.actorId && (
          <div className="filter-chip">
            <span title={filter.actorId}>
              {t('audit.staffFilter', { id: shortId(filter.actorId) })}
            </span>
            <button
              type="button"
              className="button"
              onClick={() => {
                setFilter('actorId', undefined);
              }}
            >
              {t('audit.clearStaff')}
            </button>
          </div>
        )}
        {hasFilters && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setParams(new URLSearchParams(), { replace: true });
            }}
          >
            {t('audit.reset')}
          </button>
        )}
      </form>
      {invertedRange ? (
        <p role="alert" className="list-error">
          {t('audit.invertedRange')}
        </p>
      ) : (
        // A new filter starts from a clean list: no rows or answers of the previous one leak in.
        <AuditEventList
          key={params.toString()}
          filter={filter}
          onSelectStaff={(id) => {
            setFilter('actorId', id);
          }}
        />
      )}
    </>
  );
}

type ListStatus =
  'loading' | 'ready' | 'failed' | 'forbidden' | 'rejected' | 'loadingMore' | 'moreFailed';

interface ListState {
  status: ListStatus;
  events: AuditEvent[];
  nextCursor: string | null;
}

function AuditEventList({
  filter,
  onSelectStaff,
}: {
  filter: AuditLogFilter;
  onSelectStaff: (id: string) => void;
}) {
  const { t } = useTranslation();
  const { auditLog } = useApis();
  const { signOut } = useAuth();
  const [state, setState] = useState<ListState>({
    status: 'loading',
    events: [],
    nextCursor: null,
  });
  const [attempt, setAttempt] = useState(0);

  // Retrying cannot fix a refusal (any 4xx), so only network and server failures offer it.
  const fail = useCallback(
    (error: unknown, status: 'failed' | 'moreFailed'): void => {
      const refusal = error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (refusal && error.status === 401) {
        // The session refreshes the token ahead of expiry, so a 401 means it is over: sign in again.
        void signOut();
        return;
      }
      const next: ListStatus = !refusal ? status : error.status === 403 ? 'forbidden' : 'rejected';
      setState((previous) => ({ ...previous, status: next }));
    },
    [signOut],
  );

  useEffect(() => {
    let active = true;
    auditLog.list(filter).then(
      (page) => {
        if (active) setState({ status: 'ready', events: page.items, nextCursor: page.next_cursor });
      },
      (error: unknown) => {
        if (active) fail(error, 'failed');
      },
    );
    return () => {
      active = false;
    };
  }, [auditLog, filter, attempt, fail]);

  const retry = (): void => {
    setState((previous) => ({ ...previous, status: 'loading' }));
    setAttempt((previous) => previous + 1);
  };

  const loadMore = (cursor: string): void => {
    setState((previous) => ({ ...previous, status: 'loadingMore' }));
    auditLog.list(filter, cursor).then(
      (page) => {
        setState((previous) => ({
          status: 'ready',
          events: [...previous.events, ...page.items],
          nextCursor: page.next_cursor,
        }));
      },
      (error: unknown) => {
        fail(error, 'moreFailed');
      },
    );
  };

  if (state.status === 'loading') {
    return (
      <p role="status" className="muted">
        {t('audit.loading')}
      </p>
    );
  }
  if (state.status === 'forbidden') {
    return (
      <p role="alert" className="list-error">
        {t('forbidden.text')}
      </p>
    );
  }
  if (state.status === 'rejected') {
    return (
      <p role="alert" className="list-error">
        {t('audit.rejected')}
      </p>
    );
  }
  if (state.status === 'failed') {
    return (
      <div role="alert" className="card list-alert">
        <p>{t('audit.failed')}</p>
        <button type="button" className="button" onClick={retry}>
          {t('audit.retry')}
        </button>
      </div>
    );
  }
  if (state.events.length === 0) return <p className="muted">{t('audit.empty')}</p>;

  const { nextCursor } = state;
  return (
    <>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{t('audit.columns.time')}</th>
              <th scope="col">{t('audit.columns.staff')}</th>
              <th scope="col">{t('audit.columns.action')}</th>
              <th scope="col">{t('audit.columns.target')}</th>
            </tr>
          </thead>
          <tbody>
            {state.events.map((event) => (
              <AuditEventRow key={event.id} event={event} onSelectStaff={onSelectStaff} />
            ))}
          </tbody>
        </table>
      </div>
      {state.status === 'moreFailed' && (
        <p role="alert" className="list-error">
          {t('audit.moreFailed')}
        </p>
      )}
      {nextCursor && (
        <button
          type="button"
          className="button list-more"
          disabled={state.status === 'loadingMore'}
          onClick={() => {
            loadMore(nextCursor);
          }}
        >
          {state.status === 'loadingMore' ? t('audit.loadingMore') : t('audit.loadMore')}
        </button>
      )}
    </>
  );
}

function AuditEventRow({
  event,
  onSelectStaff,
}: {
  event: AuditEvent;
  onSelectStaff: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const actorId = event.actor_id;

  return (
    <tr>
      <td>
        <time dateTime={event.occurred_at}>
          {formatMinskTime(event.occurred_at, i18n.language)}
        </time>
      </td>
      <td>
        {event.actor_type === 'staff' && actorId ? (
          <button
            type="button"
            className="link-button"
            title={actorId}
            aria-label={t('audit.filterByStaff', { id: shortId(actorId) })}
            onClick={() => {
              onSelectStaff(actorId);
            }}
          >
            {shortId(actorId)}
          </button>
        ) : event.actor_type === 'client' && actorId ? (
          <span title={actorId}>{t('audit.actor.client', { id: shortId(actorId) })}</span>
        ) : (
          t('audit.actor.system')
        )}
      </td>
      {/* An action newer than this build of the panel still shows, by its code. */}
      <td>{isAuditAction(event.action) ? t(actionLabelKey(event.action)) : event.action}</td>
      <td>
        {event.target ? (
          <span title={event.target.id}>
            {event.target.type} {shortId(event.target.id)}
          </span>
        ) : (
          <span className="muted">{t('audit.noTarget')}</span>
        )}
      </td>
    </tr>
  );
}
