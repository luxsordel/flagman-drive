import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError, FALLBACK_RETRY_AFTER_SECONDS } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { SupportPhoto } from '../support/SupportPhoto';
import { formatKopecks } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';
import {
  fineCommentMaxLength,
  fineMaxPhotos,
  fineReasonMaxLength,
  type Fine,
  type FineType,
} from './api';
import { fineName } from './format';

type FineProblemKey = 'notFinished' | 'invalid' | 'photo' | 'charged' | 'rateLimited' | 'failed';

interface FineProblem {
  key: FineProblemKey;
  /** From `Retry-After`; a proxy's 429 may come without it. */
  retryAfterSeconds?: number | undefined;
}

/** Fines share the staff money limit with the rental's adjustments: say how long to wait. */
function rateLimited(error: unknown): FineProblem | null {
  if (error instanceof ApiError && error.status === 429)
    return { key: 'rateLimited', retryAfterSeconds: error.retryAfterSeconds };
  return null;
}

function fineProblem(error: unknown): FineProblem {
  const limited = rateLimited(error);
  if (limited) return limited;
  if (error instanceof ApiError) {
    if (error.problem === 'fine-rental-not-finished') return { key: 'notFinished' };
    // The first try went through though its answer was lost, and the form was changed since.
    if (error.problem === 'idempotency-key-reused') return { key: 'charged' };
    if (error.status === 422) return { key: 'invalid' };
  }
  return { key: 'failed' };
}

function ProblemAlert({ problem, inline }: { problem: FineProblem; inline?: boolean }) {
  const { t } = useTranslation();
  const text = t(`fines.problems.${problem.key}`, {
    seconds: problem.retryAfterSeconds ?? FALLBACK_RETRY_AFTER_SECONDS,
  });
  return inline ? (
    <span role="alert" className="list-error">
      {' '}
      {text}
    </span>
  ) : (
    <p role="alert" className="list-error">
      {text}
    </p>
  );
}

function CancelForm({ fine, onCancelled }: { fine: Fine; onCancelled: (fine: Fine) => void }) {
  const { t } = useTranslation();
  const { fines } = useApis();
  const { signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<FineProblem | null>(null);

  if (!open) {
    return (
      <button
        type="button"
        className="link-button"
        onClick={() => {
          setOpen(true);
        }}
      >
        {fine.status === 'cancelled' ? t('fines.retryRefund') : t('fines.cancel')}
      </button>
    );
  }
  return (
    <form
      aria-label={t('fines.cancelTitle')}
      onSubmit={(event) => {
        event.preventDefault();
        if (!window.confirm(t('fines.cancelConfirm'))) return;
        setBusy(true);
        setProblem(null);
        fines
          .cancel(fine.id, reason.trim())
          .then(
            (updated) => {
              setOpen(false);
              setReason('');
              onCancelled(updated);
            },
            (error: unknown) => {
              if (error instanceof ApiError && error.status === 401) void signOut();
              else setProblem(rateLimited(error) ?? { key: 'failed' });
            },
          )
          .finally(() => {
            setBusy(false);
          });
      }}
    >
      <label className="stacked-label">
        {t('fines.reason')}
        <textarea
          className="field textarea"
          rows={2}
          maxLength={fineReasonMaxLength}
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
          }}
        />
      </label>
      <button type="submit" className="button" disabled={busy || !reason.trim()}>
        {t('fines.cancelSubmit')}
      </button>
      {problem && <ProblemAlert problem={problem} inline />}
    </form>
  );
}

function AddPhotos({ fine, onAdded }: { fine: Fine; onAdded: (fine: Fine) => void }) {
  const { t } = useTranslation();
  const { fines } = useApis();
  const { signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const upload = async (files: File[]) => {
    for (const file of files) onAdded(await fines.addPhoto(fine.id, file));
  };

  return (
    <label className="stacked-label">
      {t('fines.addPhoto')}
      <input
        className="field"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        disabled={busy}
        onChange={(event) => {
          const input = event.target;
          const files = Array.from(input.files ?? []);
          if (files.length === 0) return;
          setBusy(true);
          setFailed(false);
          upload(files)
            .catch((error: unknown) => {
              if (error instanceof ApiError && error.status === 401) void signOut();
              else setFailed(true);
            })
            .finally(() => {
              input.value = '';
              setBusy(false);
            });
        }}
      />
      {failed && (
        <span role="alert" className="list-error">
          {t('fines.problems.failed')}
        </span>
      )}
    </label>
  );
}

/**
 * The fines of one rental in its card: what was charged, its evidence and history, a form to
 * charge a manual fine once the trip has ended, and the cancellation with a reason.
 */
export function RentalFines({ rentalId, ended }: { rentalId: string; ended: boolean }) {
  const { t, i18n } = useTranslation();
  const { fines } = useApis();
  const { signOut } = useAuth();
  const [items, setItems] = useState<Fine[]>([]);
  // Bumped to load the list again, after a charge whose answer was lost.
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<'loading' | 'ready' | 'failed'>('loading');
  // One per filled form: a retry after a lost answer charges nothing twice.
  const [chargeKey, setChargeKey] = useState(() => crypto.randomUUID());
  const [types, setTypes] = useState<FineType[]>([]);
  const [typeId, setTypeId] = useState('');
  const [comment, setComment] = useState('');
  const [photos, setPhotos] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<FineProblem | null>(null);

  const refused = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) void signOut();
      else setProblem({ key: 'failed' });
    },
    [signOut],
  );

  useEffect(() => {
    let active = true;
    setLoaded('loading');
    fines.list({ rentalId }).then(
      (page) => {
        if (!active) return;
        setItems(page.items);
        setLoaded('ready');
      },
      (error: unknown) => {
        if (!active) return;
        setLoaded('failed');
        refused(error);
      },
    );
    if (ended) {
      fines.types().then(
        (loaded) => {
          if (active) setTypes(loaded.filter((type) => type.kind === 'manual'));
        },
        (error: unknown) => {
          if (active) refused(error);
        },
      );
    }
    return () => {
      active = false;
    };
  }, [fines, rentalId, ended, refused, reload]);

  const replaceFine = (updated: Fine) => {
    setItems((known) => known.map((item) => (item.id === updated.id ? updated : item)));
  };

  const charge = async (type: FineType) => {
    let fine = await fines.charge(rentalId, type.fine_type_id, comment.trim(), chargeKey);
    setItems((known) => [fine, ...known]);
    for (const file of photos) {
      try {
        fine = await fines.addPhoto(fine.id, file);
      } catch (error) {
        // The fine is charged already: a lost photo is told, not a reason to undo it.
        if (error instanceof ApiError && error.status === 401) throw error;
        setProblem({ key: 'photo' });
      }
    }
    replaceFine(fine);
  };

  const money = (value: number) => formatKopecks(value, i18n.language);
  const time = (iso: string) => <time dateTime={iso}>{formatMinskTime(iso, i18n.language)}</time>;
  const selected = types.find((type) => type.fine_type_id === typeId);

  return (
    <section>
      <h2>{t('fines.ofRental')}</h2>
      {loaded === 'loading' && <p className="muted">{t('fines.loading')}</p>}
      {loaded === 'ready' && items.length === 0 && (
        <p className="muted">{t('fines.noneForRental')}</p>
      )}
      {items.length > 0 && (
        <ul className="fine-list">
          {items.map((fine) => (
            <li key={fine.id} className="fine-item">
              <p>
                <strong>{fineName(fine, i18n.language)}</strong> · {money(fine.amount)} ·{' '}
                {t(`fines.statuses.${fine.status}`)}
                {fine.refund_status && <> · {t(`fines.refunds.${fine.refund_status}`)}</>}
              </p>
              {fine.comment && <p>{fine.comment}</p>}
              {fine.cancel_reason && (
                <p className="muted">
                  {t('fines.cancelledBecause', { reason: fine.cancel_reason })}
                </p>
              )}
              {fine.photos.length > 0 && (
                <ul className="support-photos">
                  {fine.photos.map((photo) => (
                    <li key={photo.id}>
                      <SupportPhoto load={() => fines.photoUrl(fine.id, photo.id)} />
                    </li>
                  ))}
                </ul>
              )}
              <ol className="support-history">
                {fine.events.map((event, index) => (
                  <li key={`${event.action}-${String(index)}`}>
                    {time(event.created_at)} {t(`fines.events.${event.action}`)}
                    {event.staff_name ? ` · ${event.staff_name}` : ''}
                    {event.comment ? `: ${event.comment}` : ''}
                  </li>
                ))}
              </ol>
              {fine.status !== 'cancelled' && fine.photos.length < fineMaxPhotos && (
                <AddPhotos fine={fine} onAdded={replaceFine} />
              )}
              {(fine.status !== 'cancelled' || fine.refund_status === 'failed') && (
                <CancelForm fine={fine} onCancelled={replaceFine} />
              )}
            </li>
          ))}
        </ul>
      )}
      {ended && (
        <form
          className="decision-form"
          aria-label={t('fines.chargeTitle')}
          onSubmit={(event) => {
            event.preventDefault();
            if (!selected) return;
            const question = t('fines.chargeConfirm', {
              name: fineName(selected, i18n.language),
              amount: money(selected.amount),
            });
            if (!window.confirm(question)) return;
            setBusy(true);
            setProblem(null);
            charge(selected)
              .then(
                () => {
                  setTypeId('');
                  setComment('');
                  setPhotos([]);
                  setChargeKey(crypto.randomUUID());
                },
                (error: unknown) => {
                  if (error instanceof ApiError && error.status === 401) {
                    void signOut();
                    return;
                  }
                  const found = fineProblem(error);
                  setProblem(found);
                  if (found.key === 'charged') {
                    setReload((count) => count + 1);
                    setChargeKey(crypto.randomUUID());
                  }
                },
              )
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <h3>{t('fines.chargeTitle')}</h3>
          <div className="filters">
            <label className="stacked-label">
              {t('fines.type')}
              <select
                className="field"
                value={typeId}
                onChange={(event) => {
                  setTypeId(event.target.value);
                }}
              >
                <option value="">{t('fines.pickType')}</option>
                {types.map((type) => (
                  <option key={type.fine_type_id} value={type.fine_type_id}>
                    {fineName(type, i18n.language)} · {money(type.amount)}
                  </option>
                ))}
              </select>
            </label>
            <label className="stacked-label">
              {t('fines.photos')}
              <input
                // A new form after a charge: the files of the last one are not shown as chosen.
                key={chargeKey}
                className="field"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                onChange={(event) => {
                  setPhotos(Array.from(event.target.files ?? []));
                }}
              />
            </label>
          </div>
          <label className="stacked-label">
            {t('fines.comment')}
            <textarea
              className="field textarea"
              rows={2}
              maxLength={fineCommentMaxLength}
              value={comment}
              onChange={(event) => {
                setComment(event.target.value);
              }}
            />
          </label>
          <button
            type="submit"
            className="button button-primary"
            disabled={busy || !selected || !comment.trim()}
          >
            {t('fines.chargeSubmit')}
          </button>
          {problem && <ProblemAlert problem={problem} />}
        </form>
      )}
      {!ended && problem && <ProblemAlert problem={problem} />}
    </section>
  );
}
