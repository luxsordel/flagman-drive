import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import type { KycQueue } from '../kyc/api';
import { formatWaiting, fullName } from '../kyc/format';
import type { KycQueueState } from '../kyc/queueState';
import { formatMinskTime } from '../time/minsk';

type QueueStatus = 'loading' | 'ready' | 'failed' | 'forbidden';

/** Applications waiting for a decision, the longest waiting first. */
export function KycQueuePage() {
  const { t, i18n } = useTranslation();
  const { kycReview } = useApis();
  const { signOut } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  // Read once: the history entry is cleared below, so a reload or Back does not repeat the message.
  const [decided] = useState((location.state as KycQueueState | null)?.decided);
  const [status, setStatus] = useState<QueueStatus>('loading');
  const [queue, setQueue] = useState<KycQueue>({ items: [], total: 0 });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (decided) void navigate('.', { replace: true, state: null });
  }, [decided, navigate]);

  useEffect(() => {
    let active = true;
    kycReview.queue().then(
      (loaded) => {
        if (!active) return;
        setQueue(loaded);
        setStatus('ready');
      },
      (error: unknown) => {
        if (!active) return;
        // The session refreshes the token ahead of expiry, so a 401 means it is over.
        if (error instanceof ApiError && error.status === 401) void signOut();
        else setStatus(error instanceof ApiError && error.status === 403 ? 'forbidden' : 'failed');
      },
    );
    return () => {
      active = false;
    };
  }, [kycReview, attempt, signOut]);

  const now = new Date();

  return (
    <>
      <h1>{t('nav.kyc')}</h1>
      {decided && (
        <p role="status" className="notice">
          {t(`kyc.queue.${decided}`)}
        </p>
      )}
      {status === 'loading' && (
        <p role="status" className="muted">
          {t('kyc.queue.loading')}
        </p>
      )}
      {status === 'forbidden' && (
        <p role="alert" className="list-error">
          {t('forbidden.text')}
        </p>
      )}
      {status === 'failed' && (
        <div role="alert" className="card list-alert">
          <p>{t('kyc.queue.failed')}</p>
          <button
            type="button"
            className="button"
            onClick={() => {
              setStatus('loading');
              setAttempt((previous) => previous + 1);
            }}
          >
            {t('kyc.retry')}
          </button>
        </div>
      )}
      {status === 'ready' && queue.items.length === 0 && (
        <p className="muted">{t('kyc.queue.empty')}</p>
      )}
      {status === 'ready' && queue.items.length > 0 && (
        <>
          <p className="muted">{t('kyc.queue.total', { count: queue.total })}</p>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">{t('kyc.queue.columns.client')}</th>
                  <th scope="col">{t('kyc.queue.columns.submitted')}</th>
                  <th scope="col">{t('kyc.queue.columns.waiting')}</th>
                </tr>
              </thead>
              <tbody>
                {queue.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link to={`/kyc/${item.id}`}>{fullName(item) ?? t('kyc.noName')}</Link>
                    </td>
                    <td>
                      <time dateTime={item.submitted_at}>
                        {formatMinskTime(item.submitted_at, i18n.language)}
                      </time>
                    </td>
                    <td>{formatWaiting(item.submitted_at, now, i18n.language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {queue.total > queue.items.length && (
            <p className="muted list-more">{t('kyc.queue.shown', { shown: queue.items.length })}</p>
          )}
        </>
      )}
    </>
  );
}
