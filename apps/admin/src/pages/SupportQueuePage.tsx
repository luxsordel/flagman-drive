import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { fullName } from '../kyc/format';
import type { Conversation } from '../support/api';
import { listPollMs, useInterval } from '../support/useInterval';
import { formatMinskTime } from '../time/minsk';

type QueueStatus = 'loading' | 'ready' | 'failed' | 'forbidden';

/** Open conversations: the API puts those with unread messages first. */
export function SupportQueuePage() {
  const { t, i18n } = useTranslation();
  const { support } = useApis();
  const { signOut } = useAuth();
  const [status, setStatus] = useState<QueueStatus>('loading');
  const [items, setItems] = useState<Conversation[]>([]);

  const load = useCallback(
    () =>
      support.queue().then(
        (loaded) => {
          setItems(loaded);
          setStatus('ready');
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) void signOut();
          else if (error instanceof ApiError && error.status === 403) setStatus('forbidden');
          // A failed refresh keeps the list on the screen; the next one will try again.
          else setStatus((previous) => (previous === 'ready' ? previous : 'failed'));
        },
      ),
    [support, signOut],
  );

  useEffect(() => {
    void load();
  }, [load]);
  useInterval(() => {
    void load();
  }, listPollMs);

  return (
    <>
      <h1>{t('nav.support')}</h1>
      {status === 'loading' && (
        <p role="status" className="muted">
          {t('support.loading')}
        </p>
      )}
      {status === 'forbidden' && (
        <p role="alert" className="list-error">
          {t('forbidden.text')}
        </p>
      )}
      {status === 'failed' && (
        <div role="alert" className="card list-alert">
          <p>{t('support.failed')}</p>
          <button
            type="button"
            className="button"
            onClick={() => {
              setStatus('loading');
              void load();
            }}
          >
            {t('support.retry')}
          </button>
        </div>
      )}
      {status === 'ready' && items.length === 0 && <p className="muted">{t('support.empty')}</p>}
      {status === 'ready' && items.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t('support.columns.client')}</th>
                <th scope="col">{t('support.columns.lastMessage')}</th>
                <th scope="col">{t('support.columns.unread')}</th>
                <th scope="col">{t('support.columns.car')}</th>
                <th scope="col">{t('support.columns.operator')}</th>
                <th scope="col">{t('support.columns.time')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={`/support/${item.id}`}>{fullName(item) ?? t('support.noName')}</Link>
                  </td>
                  <td>{item.last_message_text ?? t('support.photo')}</td>
                  <td>{item.unread || ''}</td>
                  <td>{item.car_plate ?? ''}</td>
                  <td>{item.assigned_staff_name ?? ''}</td>
                  <td>
                    <time dateTime={item.last_message_at}>
                      {formatMinskTime(item.last_message_at, i18n.language)}
                    </time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
