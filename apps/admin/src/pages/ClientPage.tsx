import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { loadProblem, type LoadProblem } from '../api/loadProblem';
import { useAuth } from '../auth/context';
import { blockReasonMaxLength, type StaffClient } from '../clients/api';
import { formatCalendarDay, fullName } from '../kyc/format';
import { ClientRentals } from '../rentals/ClientRentals';
import { formatKopecks } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';

type BlockProblem = 'reason' | 'forbidden' | 'notFound' | 'failed';

/** A retry helps only with `failed`: a refused reason or a missing client stay refused. */
function blockProblem(error: unknown): BlockProblem {
  if (error instanceof ApiError) {
    if (error.status === 422) return 'reason';
    if (error.status === 403) return 'forbidden';
    if (error.status === 404) return 'notFound';
  }
  return 'failed';
}

/** A client for an operator: who they are, whether they may rent, and the block. */
export function ClientPage() {
  const { t, i18n } = useTranslation();
  const { clientId = '' } = useParams();
  const { clients } = useApis();
  const { signOut } = useAuth();
  const [client, setClient] = useState<StaffClient | null>(null);
  const [loadFailed, setLoadFailed] = useState<LoadProblem | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<BlockProblem | null>(null);

  const refused = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setLoadFailed(loadProblem(error));
    },
    [signOut],
  );

  useEffect(() => {
    // The answer for the page before is dropped: it would show another one here.
    let active = true;
    clients.client(clientId).then(
      (loaded) => {
        if (!active) return;
        setClient(loaded);
        setLoadFailed(null);
      },
      (error: unknown) => {
        if (active) refused(error);
      },
    );
    return () => {
      active = false;
    };
  }, [clients, clientId, refused]);

  if (!client) {
    return loadFailed ? (
      <p role="alert" className="list-error">
        {t(`load.${loadFailed}`)}
      </p>
    ) : (
      <p role="status" className="muted">
        {t('support.loading')}
      </p>
    );
  }

  const blocked = client.block !== null;
  const change = () => {
    const question = t(blocked ? 'clients.unblockConfirm' : 'clients.blockConfirm');
    if (!window.confirm(question)) return;
    setBusy(true);
    setFailed(null);
    const sent = blocked
      ? clients.unblock(client.id, reason.trim())
      : clients.block(client.id, reason.trim());
    sent
      .then(
        (updated) => {
          setClient(updated);
          setReason('');
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) void signOut();
          else setFailed(blockProblem(error));
        },
      )
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <>
      <p>
        <Link to="/clients">{t('clients.back')}</Link>
      </p>
      <h1>{fullName(client) ?? t('support.noName')}</h1>
      <dl className="details support-details">
        <dt>{t('clients.phone')}</dt>
        <dd>{client.phone}</dd>
        <dt>{t('clients.birthDate')}</dt>
        <dd>{client.birth_date ? formatCalendarDay(client.birth_date, i18n.language) : '—'}</dd>
        <dt>{t('clients.email')}</dt>
        <dd>{client.email ?? '—'}</dd>
        <dt>{t('clients.signedUp')}</dt>
        <dd>
          <time dateTime={client.created_at}>
            {formatMinskTime(client.created_at, i18n.language)}
          </time>
        </dd>
        <dt>{t('clients.kyc')}</dt>
        <dd>{t(`clients.kycStatuses.${client.kyc_status}`)}</dd>
        <dt>{t('clients.debt')}</dt>
        <dd>{formatKopecks(client.debt, i18n.language)}</dd>
      </dl>
      <ClientRentals clientId={client.id} />
      <p>
        <Link to={`/fines?client=${client.id}`}>{t('clients.fines')}</Link>
      </p>
      {client.block && (
        <p role="status" className="list-error">
          {t('clients.blockedBy', {
            name: client.block.staff_name ?? t('support.nobody'),
            reason: client.block.reason,
          })}
        </p>
      )}
      <section className="decision-form">
        <h2>{blocked ? t('clients.unblock') : t('clients.block')}</h2>
        <label className="stacked-label">
          {t('clients.reason')}
          <textarea
            className="field textarea"
            rows={2}
            maxLength={blockReasonMaxLength}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </label>
        {failed && (
          <p role="alert" className="list-error">
            {t(`clients.problems.${failed}`)}
          </p>
        )}
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !reason.trim()}
          onClick={change}
        >
          {blocked ? t('clients.unblock') : t('clients.block')}
        </button>
      </section>
      <section>
        <h2>{t('clients.history')}</h2>
        {client.block_history.length === 0 ? (
          <p className="muted">{t('clients.noHistory')}</p>
        ) : (
          <ol className="support-history">
            {client.block_history.map((event) => (
              <li key={event.created_at}>
                <time dateTime={event.created_at}>
                  {formatMinskTime(event.created_at, i18n.language)}
                </time>{' '}
                {event.blocked ? t('clients.wasBlocked') : t('clients.wasUnblocked')}
                {event.staff_name ? ` · ${event.staff_name}` : ''}: {event.reason}
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}
