import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { fullName } from '../kyc/format';
import { SupportPhoto } from '../support/SupportPhoto';
import type { Incident } from '../support/api';
import { listPollMs, useInterval } from '../support/useInterval';
import { formatMinskTime } from '../time/minsk';

// The map library is large: it loads only for an incident that has a place.
const IncidentMap = lazy(() =>
  import('../support/IncidentMap').then(({ IncidentMap }) => ({ default: IncidentMap })),
);

type ActionProblem = 'taken' | 'carBusy' | 'rateLimited' | 'failed';

function actionProblem(error: unknown): ActionProblem {
  if (error instanceof ApiError) {
    if (error.problem === 'car-transition-not-allowed') return 'carBusy';
    if (error.status === 409) return 'taken';
    if (error.status === 429) return 'rateLimited';
  }
  return 'failed';
}

/** One incident: what the client reported, the history and what the operator can do next. */
export function IncidentPage() {
  const { t, i18n } = useTranslation();
  const { incidentId = '' } = useParams();
  const { support } = useApis();
  const { signOut } = useAuth();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ActionProblem | null>(null);

  const refresh = useCallback(
    () =>
      support.incident(incidentId).then(
        (loaded) => {
          setIncident(loaded);
          setLoadFailed(false);
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) void signOut();
          else setLoadFailed(true);
        },
      ),
    [support, incidentId, signOut],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useInterval(() => {
    void refresh();
  }, listPollMs);

  const act = (action: () => Promise<Incident>) => {
    setBusy(true);
    setProblem(null);
    action()
      .then(
        (updated) => {
          setIncident(updated);
          setComment('');
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) void signOut();
          else setProblem(actionProblem(error));
        },
      )
      .finally(() => {
        setBusy(false);
      });
  };

  if (!incident) {
    return loadFailed ? (
      <p role="alert" className="list-error">
        {t('support.failed')}
      </p>
    ) : (
      <p role="status" className="muted">
        {t('support.loading')}
      </p>
    );
  }

  const decidable = incident.status === 'in_progress';
  const noComment = !comment.trim();
  return (
    <>
      <p>
        <Link to="/incidents">{t('incidents.back')}</Link>
      </p>
      <h1>{t(`incidents.types.${incident.type}`)}</h1>
      <dl className="details support-details">
        <dt>{t('incidents.status')}</dt>
        <dd>{t(`incidents.statuses.${incident.status}`)}</dd>
        <dt>{t('support.columns.client')}</dt>
        <dd>{fullName(incident) ?? t('support.noName')}</dd>
        <dt>{t('support.columns.car')}</dt>
        <dd>{incident.car_plate ?? t('support.noRental')}</dd>
        {incident.rental_status && (
          <>
            <dt>{t('support.rental')}</dt>
            <dd>{t(`support.rentalStatus.${incident.rental_status}`)}</dd>
          </>
        )}
        <dt>{t('support.columns.operator')}</dt>
        <dd>{incident.assigned_staff_name ?? t('support.nobody')}</dd>
        <dt>{t('incidents.created')}</dt>
        <dd>
          <time dateTime={incident.created_at}>
            {formatMinskTime(incident.created_at, i18n.language)}
          </time>
        </dd>
      </dl>
      <p className="support-description">{incident.description}</p>
      {loadFailed && (
        <p role="alert" className="list-error">
          {t('support.stale')}
        </p>
      )}
      {incident.location && (
        <section>
          <h2>{t('incidents.place')}</h2>
          <p className="muted">
            {incident.location.lat}, {incident.location.lon}
          </p>
          <Suspense fallback={null}>
            <IncidentMap
              lat={incident.location.lat}
              lon={incident.location.lon}
              label={t('incidents.place')}
            />
          </Suspense>
        </section>
      )}
      {incident.photos.length > 0 && (
        <section>
          <h2>{t('incidents.photos')}</h2>
          <ul className="support-photos">
            {incident.photos.map((photo) => (
              <li key={photo.id}>
                <SupportPhoto load={() => support.incidentPhotoUrl(incident.id, photo.id)} />
              </li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <h2>{t('incidents.history')}</h2>
        {incident.events.length === 0 ? (
          <p className="muted">{t('incidents.noHistory')}</p>
        ) : (
          <ol className="support-history">
            {incident.events.map((event) => (
              <li key={`${event.action}-${event.created_at}`}>
                <time dateTime={event.created_at}>
                  {formatMinskTime(event.created_at, i18n.language)}
                </time>{' '}
                {t(`incidents.actions.${event.action}`)}
                {event.staff_name ? ` · ${event.staff_name}` : ''}
                {event.comment ? `: ${event.comment}` : ''}
              </li>
            ))}
          </ol>
        )}
      </section>
      {problem && (
        <p role="alert" className="list-error">
          {t(`incidents.problems.${problem}`)}
        </p>
      )}
      <div className="support-actions">
        {incident.status === 'new' && (
          <button
            type="button"
            className="button button-primary"
            disabled={busy}
            onClick={() => {
              act(() => support.takeIncident(incident.id));
            }}
          >
            {t('support.take')}
          </button>
        )}
        {incident.car_id && !incident.sent_to_service && (
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => {
              if (window.confirm(t('incidents.sendConfirm'))) {
                act(() => support.sendToService(incident.id));
              }
            }}
          >
            {t('incidents.sendToService')}
          </button>
        )}
      </div>
      {decidable && (
        <div className="decision-form">
          <label className="stacked-label">
            {t('incidents.comment')}
            <textarea
              className="field textarea"
              rows={3}
              value={comment}
              onChange={(event) => {
                setComment(event.target.value);
              }}
            />
          </label>
          <div className="support-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={busy || noComment}
              onClick={() => {
                act(() => support.resolveIncident(incident.id, comment.trim()));
              }}
            >
              {t('incidents.resolve')}
            </button>
            <button
              type="button"
              className="button"
              disabled={busy || noComment}
              onClick={() => {
                act(() => support.rejectIncident(incident.id, comment.trim()));
              }}
            >
              {t('incidents.reject')}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
