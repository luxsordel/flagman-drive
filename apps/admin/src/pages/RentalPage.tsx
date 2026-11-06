import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { loadProblem, type LoadProblem } from '../api/loadProblem';
import { useAuth } from '../auth/context';
import { RentalFines } from '../fines/RentalFines';
import { fullName } from '../kyc/format';
import { adjustmentMaxKopecks, staffReasonMaxLength, type StaffRental } from '../rentals/api';
import { SupportPhoto } from '../support/SupportPhoto';
import { formatKopecks, parseRubles } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';

type ActionProblem =
  | 'conflict'
  | 'busy'
  | 'pending'
  | 'invalid'
  | 'rejected'
  | 'reason'
  | 'reused'
  | 'paymentsUnavailable'
  | 'rateLimited'
  | 'failed';

interface ActionError {
  key: ActionProblem;
  /** From `Retry-After`; a proxy's 429 may come without it. */
  retryAfterSeconds?: number | undefined;
}

/** `action` tells a refused reason of a forced finish from a refused amount of an adjustment. */
function actionProblem(error: unknown, action: 'force' | 'adjust' | 'refund'): ActionError {
  if (error instanceof ApiError) {
    // A forced finish shares the staff command limit with the fleet page: say how long to wait.
    if (error.status === 429)
      return { key: 'rateLimited', retryAfterSeconds: error.retryAfterSeconds };
    if (error.problem === 'rental-settlement-pending') return { key: 'pending' };
    if (error.problem === 'idempotency-key-reused') return { key: 'reused' };
    if (error.problem === 'rental-command-in-progress') return { key: 'busy' };
    if (error.problem === 'payments-unavailable') return { key: 'paymentsUnavailable' };
    if (error.status === 422) return { key: action === 'force' ? 'reason' : 'rejected' };
    if (error.problem === 'rental-adjustment-invalid') return { key: 'invalid' };
    if (error.status === 409) return { key: 'conflict' };
  }
  return { key: 'failed' };
}

const driving = new Set(['in_trip', 'parked']);
const settled = new Set(['finished', 'paid', 'payment_failed', 'cancelled']);

/** One rental for an operator: what it cost, how it was paid, and the two ways to fix it. */
export function RentalPage() {
  const { t, i18n } = useTranslation();
  const { rentalId = '' } = useParams();
  const { rentals } = useApis();
  const { signOut } = useAuth();
  const [card, setCard] = useState<StaffRental | null>(null);
  const [loadFailed, setLoadFailed] = useState<LoadProblem | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ActionError | null>(null);
  const [notLocked, setNotLocked] = useState(false);
  const [reason, setReason] = useState('');
  const [direction, setDirection] = useState<'down' | 'up'>('down');
  const [amount, setAmount] = useState('');
  const [comment, setComment] = useState('');
  // One per filled form: a retry after a lost answer sends the same key and moves no money again.
  const [adjustKey, setAdjustKey] = useState(() => crypto.randomUUID());

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
    rentals.rental(rentalId).then(
      (loaded) => {
        if (!active) return;
        setCard(loaded);
        setLoadFailed(null);
      },
      (error: unknown) => {
        if (active) refused(error);
      },
    );
    return () => {
      active = false;
    };
  }, [rentals, rentalId, refused]);

  const act = (
    kind: 'force' | 'adjust' | 'refund',
    action: () => Promise<StaffRental>,
    done: () => void,
  ) => {
    setBusy(true);
    setProblem(null);
    action()
      .then(
        (updated) => {
          setCard(updated);
          done();
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) void signOut();
          else setProblem(actionProblem(error, kind));
        },
      )
      .finally(() => {
        setBusy(false);
      });
  };

  if (!card) {
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

  const { rental } = card;
  const kopecks = parseRubles(amount);
  const amountValid = kopecks !== null && kopecks > 0 && kopecks <= adjustmentMaxKopecks;
  const lostRefunds = card.operations.some(
    (op) => op.kind === 'refund' && op.status !== 'succeeded' && op.status !== 'declined',
  );
  const money = (value: number) => formatKopecks(value, i18n.language);
  const time = (iso: string) => <time dateTime={iso}>{formatMinskTime(iso, i18n.language)}</time>;

  return (
    <>
      <p>
        <Link to="/rentals">{t('rentals.back')}</Link>
      </p>
      <h1>{t('rentals.title', { plate: rental.car.plate })}</h1>
      <dl className="details support-details">
        <dt>{t('rentals.status')}</dt>
        <dd>{t(`support.rentalStatus.${rental.status}`)}</dd>
        <dt>{t('support.columns.client')}</dt>
        <dd>
          <Link to={`/clients/${card.client.id}`}>
            {fullName(card.client) ?? t('support.noName')}
          </Link>
        </dd>
        <dt>{t('support.columns.car')}</dt>
        <dd>
          {rental.car.brand} {rental.car.model}, {rental.car.plate}
        </dd>
        <dt>{t('rentals.reserved')}</dt>
        <dd>{time(rental.reserved_at)}</dd>
        {rental.trip_started_at && (
          <>
            <dt>{t('rentals.started')}</dt>
            <dd>{time(rental.trip_started_at)}</dd>
          </>
        )}
        {rental.ended_at && (
          <>
            <dt>{t('rentals.ended')}</dt>
            <dd>{time(rental.ended_at)}</dd>
          </>
        )}
        {rental.start && (
          <>
            <dt>{t('rentals.startPoint')}</dt>
            <dd>
              {rental.start.lat}, {rental.start.lon}
            </dd>
          </>
        )}
        {rental.end && (
          <>
            <dt>{t('rentals.endPoint')}</dt>
            <dd>
              {rental.end.lat}, {rental.end.lon}
            </dd>
          </>
        )}
        <dt>{t('rentals.tariff')}</dt>
        <dd>
          {t('rentals.prices', {
            driving: money(rental.tariff.driving_per_minute),
            parked: money(rental.tariff.parked_per_minute),
            waiting: money(rental.tariff.waiting_per_minute),
          })}
        </dd>
      </dl>
      {card.forced_finish && (
        <p className="muted">
          {t('rentals.forcedBy', {
            name: card.forced_finish.staff_name ?? t('support.nobody'),
            reason: card.forced_finish.reason,
          })}
        </p>
      )}
      {notLocked && (
        <p role="alert" className="list-error">
          {t('rentals.notLocked')}
        </p>
      )}

      <section>
        <h2>{t('rentals.money')}</h2>
        <table className="table">
          <tbody>
            {rental.cost.lines.map((line) => (
              <tr key={line.kind}>
                <th scope="row">{t(`rentals.lines.${line.kind}`)}</th>
                <td>{t('rentals.minutes', { count: line.minutes })}</td>
                <td>{money(line.amount)}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">{t('rentals.receiptTotal')}</th>
              <td />
              <td>{money(rental.cost.total)}</td>
            </tr>
            {card.adjustments.map((adjustment) => (
              <tr key={adjustment.id}>
                <th scope="row">
                  {t('rentals.adjustment')} · {adjustment.staff_name ?? t('support.nobody')}
                </th>
                <td>{adjustment.comment}</td>
                <td>{money(adjustment.amount)}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">{t('rentals.total')}</th>
              <td />
              <td>
                <strong>{money(rental.total)}</strong>
              </td>
            </tr>
          </tbody>
        </table>
        {card.owed > 0 && (
          <p className="list-error">{t('rentals.owed', { amount: money(card.owed) })}</p>
        )}
        {card.owed < 0 && (
          <p className="list-error">{t('rentals.overpaid', { amount: money(-card.owed) })}</p>
        )}
      </section>

      <section>
        <h2>{t('rentals.operations')}</h2>
        {card.operations.length === 0 ? (
          <p className="muted">{t('rentals.noOperations')}</p>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">{t('rentals.when')}</th>
                  <th scope="col">{t('rentals.kind')}</th>
                  <th scope="col">{t('rentals.amount')}</th>
                  <th scope="col">{t('rentals.status')}</th>
                </tr>
              </thead>
              <tbody>
                {card.operations.map((op) => (
                  <tr key={op.id}>
                    <td>{time(op.created_at)}</td>
                    <td>{t(`rentals.operationKinds.${op.kind}`)}</td>
                    <td>{money(op.amount)}</td>
                    <td>{t(`rentals.operationStatuses.${op.status}`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2>{t('rentals.intervals')}</h2>
        {card.intervals.length === 0 ? (
          <p className="muted">{t('rentals.noTrip')}</p>
        ) : (
          <ol className="support-history">
            {card.intervals.map((interval) => (
              <li key={interval.started_at}>
                {t(`rentals.lines.${interval.kind}`)}: {time(interval.started_at)}
                {' — '}
                {interval.ended_at ? time(interval.ended_at) : t('rentals.now')}
              </li>
            ))}
          </ol>
        )}
      </section>

      {card.inspections.map((inspection) => (
        <section key={inspection.stage}>
          <h2>{t(`rentals.inspections.${inspection.stage}`)}</h2>
          <ul className="support-photos">
            {inspection.photos.map((photo) => (
              <li key={photo.id}>
                <SupportPhoto
                  load={() => rentals.photoUrl(rental.id, inspection.stage, photo.id)}
                />
              </li>
            ))}
          </ul>
          {inspection.damages.length > 0 && (
            <ul>
              {inspection.damages.map((damage) => (
                <li key={damage.id}>
                  {t(`rentals.sides.${damage.side}`)}: {damage.comment}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      {problem && (
        <p role="alert" className="list-error">
          {t(`rentals.problems.${problem.key}`, { seconds: problem.retryAfterSeconds ?? 60 })}
        </p>
      )}

      {driving.has(rental.status) && (
        <section className="decision-form">
          <h2>{t('rentals.forceFinish')}</h2>
          <label className="stacked-label">
            {t('rentals.reason')}
            <textarea
              className="field textarea"
              rows={2}
              maxLength={staffReasonMaxLength}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
              }}
            />
          </label>
          <button
            type="button"
            className="button button-primary"
            disabled={busy || !reason.trim()}
            onClick={() => {
              if (!window.confirm(t('rentals.forceConfirm'))) return;
              let locked = true;
              act(
                'force',
                () =>
                  rentals.forceFinish(rental.id, reason.trim()).then((result) => {
                    locked = result.locked;
                    return result.rental;
                  }),
                () => {
                  setNotLocked(!locked);
                  setReason('');
                },
              );
            }}
          >
            {t('rentals.forceFinish')}
          </button>
        </section>
      )}

      {/* A reservation cancelled in its free wait has no receipt: nothing to adjust. */}
      {settled.has(rental.status) && rental.receipt !== null && (
        <section className="decision-form">
          <h2>{t('rentals.adjust')}</h2>
          <div className="filters">
            <label className="stacked-label">
              {t('rentals.direction')}
              <select
                className="field"
                value={direction}
                onChange={(event) => {
                  setDirection(event.target.value === 'up' ? 'up' : 'down');
                  setAdjustKey(crypto.randomUUID());
                }}
              >
                <option value="down">{t('rentals.decrease')}</option>
                <option value="up">{t('rentals.increase')}</option>
              </select>
            </label>
            <label className="stacked-label">
              {t('rentals.amountRubles')}
              <input
                className="field"
                inputMode="decimal"
                value={amount}
                onChange={(event) => {
                  setAmount(event.target.value);
                  setAdjustKey(crypto.randomUUID());
                }}
              />
            </label>
          </div>
          <label className="stacked-label">
            {t('rentals.comment')}
            <textarea
              className="field textarea"
              rows={2}
              maxLength={staffReasonMaxLength}
              value={comment}
              onChange={(event) => {
                setComment(event.target.value);
                setAdjustKey(crypto.randomUUID());
              }}
            />
          </label>
          <button
            type="button"
            className="button button-primary"
            disabled={busy || !amountValid || !comment.trim()}
            onClick={() => {
              if (kopecks === null) return;
              const signed = direction === 'up' ? kopecks : -kopecks;
              const question = t(
                direction === 'up' ? 'rentals.increaseConfirm' : 'rentals.decreaseConfirm',
                {
                  amount: money(kopecks),
                },
              );
              if (!window.confirm(question)) return;
              act(
                'adjust',
                () => rentals.adjust(rental.id, signed, comment.trim(), adjustKey),
                () => {
                  setAmount('');
                  setComment('');
                  setAdjustKey(crypto.randomUUID());
                },
              );
            }}
          >
            {t('rentals.adjustSubmit')}
          </button>
          {/* Also with no refund yet: a crash may have come before the provider. */}
          {(lostRefunds || card.owed < 0) && (
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => {
                act(
                  'refund',
                  () => rentals.retryRefunds(rental.id),
                  () => undefined,
                );
              }}
            >
              {t('rentals.retryRefunds')}
            </button>
          )}
        </section>
      )}

      {/* A reservation cancelled before the trip has nothing to be fined for. */}
      <RentalFines
        rentalId={rental.id}
        ended={rental.trip_started_at !== null && rental.ended_at !== null}
      />
    </>
  );
}
