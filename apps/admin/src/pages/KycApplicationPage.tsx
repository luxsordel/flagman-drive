import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
  type SyntheticEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import type { KycApplication, KycDocumentKind } from '../kyc/api';
import {
  documentKinds,
  formatCalendarDay,
  fullName,
  reasonLength,
  rejectionReasonMaxLength,
} from '../kyc/format';
import { useRefreshKycCount } from '../kyc/pendingCount';
import type { KycQueueState } from '../kyc/queueState';
import { maxZoom, minZoom, viewerLayout, zoomStep } from '../kyc/viewerLayout';
import { formatMinskTime } from '../time/minsk';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A link is asked for again this long before it expires, so a photo never opens on a dead one. */
const linkMarginMs = 10_000;

type PageState =
  | { status: 'loading' | 'failed' | 'notFound' | 'forbidden' }
  | { status: 'ready'; application: KycApplication };

/** One application: the profile, the document photos and the decision. */
export function KycApplicationPage() {
  const { applicationId = '' } = useParams();
  // The route keeps its component when only the id changes: without the key, a date or a reason
  // typed for one application would be sent for the next one.
  return <ApplicationLoader key={applicationId} applicationId={applicationId} />;
}

function ApplicationLoader({ applicationId }: { applicationId: string }) {
  const { t } = useTranslation();
  const { kycReview } = useApis();
  const { signOut } = useAuth();
  const refreshKycCount = useRefreshKycCount();
  const validId = uuid.test(applicationId);
  const [state, setState] = useState<PageState>({ status: validId ? 'loading' : 'notFound' });
  const [attempt, setAttempt] = useState(0);
  // Kept across a reload of the application: it explains why the forms disappeared.
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!validId) return;
    let active = true;
    kycReview.application(applicationId).then(
      (application) => {
        if (active) setState({ status: 'ready', application });
      },
      (error: unknown) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) {
          void signOut();
          return;
        }
        const status =
          error instanceof ApiError && error.status === 404
            ? 'notFound'
            : error instanceof ApiError && error.status === 403
              ? 'forbidden'
              : 'failed';
        setState({ status });
      },
    );
    return () => {
      active = false;
    };
  }, [kycReview, applicationId, validId, attempt, signOut]);

  const reload = useCallback(() => {
    setState({ status: 'loading' });
    setAttempt((previous) => previous + 1);
  }, []);

  // The card stays while the fresh copy loads: unmounting it would ask for every photo link again,
  // and each link is a view in the audit log that the operator did not make.
  const refreshInPlace = useCallback(() => {
    setAttempt((previous) => previous + 1);
  }, []);

  return (
    <>
      <p>
        <Link to="/kyc">{t('kyc.back')}</Link>
      </p>
      {state.status === 'loading' && (
        <p role="status" className="muted">
          {t('kyc.loading')}
        </p>
      )}
      {state.status === 'notFound' && (
        <p role="alert" className="list-error">
          {t('kyc.notFound')}
        </p>
      )}
      {state.status === 'forbidden' && (
        <p role="alert" className="list-error">
          {t('forbidden.text')}
        </p>
      )}
      {state.status === 'failed' && (
        <div role="alert" className="card list-alert">
          <p>{t('kyc.failed')}</p>
          <button type="button" className="button" onClick={reload}>
            {t('kyc.retry')}
          </button>
        </div>
      )}
      {state.status === 'ready' && (
        <ApplicationCard
          application={state.application}
          notice={notice}
          onNotPending={() => {
            setNotice(t('kyc.errors.notPending'));
            refreshInPlace();
            refreshKycCount();
          }}
        />
      )}
    </>
  );
}

function ApplicationCard({
  application,
  notice,
  onNotPending,
}: {
  application: KycApplication;
  notice: string | null;
  onNotPending: () => void;
}) {
  const { t, i18n } = useTranslation();
  const uploaded = new Set(application.documents.map((document) => document.kind));
  const notGiven = <span className="muted">{t('kyc.profile.notGiven')}</span>;

  return (
    <>
      <h1>{fullName(application) ?? t('kyc.noName')}</h1>
      {notice && (
        <p role="alert" className="list-error">
          {notice}
        </p>
      )}
      <div className="kyc-layout">
        <section className="card kyc-profile" aria-labelledby="kyc-profile-title">
          <h2 id="kyc-profile-title">{t('kyc.profile.title')}</h2>
          <p className="muted">{t('kyc.profile.hint')}</p>
          <dl className="details">
            <dt>{t('kyc.profile.lastName')}</dt>
            <dd>{application.last_name ?? notGiven}</dd>
            <dt>{t('kyc.profile.firstName')}</dt>
            <dd>{application.first_name ?? notGiven}</dd>
            <dt>{t('kyc.profile.birthDate')}</dt>
            <dd>
              {application.birth_date
                ? formatCalendarDay(application.birth_date, i18n.language)
                : notGiven}
            </dd>
            <dt>{t('kyc.profile.age')}</dt>
            <dd>{application.age_years ?? notGiven}</dd>
            <dt>{t('kyc.profile.submitted')}</dt>
            <dd>
              {application.submitted_at
                ? formatMinskTime(application.submitted_at, i18n.language)
                : notGiven}
            </dd>
            <dt>{t('kyc.profile.status')}</dt>
            <dd>{t(`kyc.status.${application.status}`)}</dd>
          </dl>
        </section>
        {application.status === 'pending' ? (
          <Decision application={application} onNotPending={onNotPending} />
        ) : (
          <section className="card">
            <p>{t(application.status === 'not_submitted' ? 'kyc.notSubmitted' : 'kyc.decided')}</p>
            {application.rejection_reason && (
              <p>{t('kyc.decidedRejected', { reason: application.rejection_reason })}</p>
            )}
          </section>
        )}
      </div>
      <section aria-labelledby="kyc-documents-title">
        <h2 id="kyc-documents-title">{t('kyc.documents.title')}</h2>
        <ul className="kyc-documents">
          {documentKinds.map((kind) => (
            <li key={kind} className="card kyc-document">
              <h3>{t(`kyc.documents.kinds.${kind}`)}</h3>
              {uploaded.has(kind) ? (
                <DocumentPhoto applicationId={application.id} kind={kind} />
              ) : (
                <p className="muted">{t('kyc.documents.missing')}</p>
              )}
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

type LinkProblem = 'failed' | 'rateLimited';

interface PhotoLink {
  url: string;
  expiresAt: number;
}

/**
 * A photo by a short-lived link. Links live only in this component: each one is in the audit log
 * and opens the photo without a token, so it is not kept or passed anywhere else.
 */
function DocumentPhoto({ applicationId, kind }: { applicationId: string; kind: KycDocumentKind }) {
  const { t } = useTranslation();
  const { kycReview } = useApis();
  const { signOut } = useAuth();
  const [link, setLink] = useState<PhotoLink | null>(null);
  const [problem, setProblem] = useState<LinkProblem | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [viewing, setViewing] = useState(false);
  // Set when the photo was opened on an expired link: it opens once the fresh one arrives.
  const openOnLink = useRef(false);
  const thumbnail = useRef<HTMLButtonElement>(null);
  const label = t(`kyc.documents.kinds.${kind}`);

  useEffect(() => {
    let active = true;
    kycReview.documentUrl(applicationId, kind).then(
      ({ url, expires_in }) => {
        if (!active) return;
        setLink({ url, expiresAt: Date.now() + expires_in * 1000 });
        if (openOnLink.current) {
          openOnLink.current = false;
          setViewing(true);
        }
      },
      (error: unknown) => {
        if (!active) return;
        openOnLink.current = false;
        if (error instanceof ApiError && error.status === 401) {
          void signOut();
          return;
        }
        setLink(null);
        setProblem(error instanceof ApiError && error.status === 429 ? 'rateLimited' : 'failed');
      },
    );
    return () => {
      active = false;
    };
  }, [kycReview, applicationId, kind, attempt, signOut]);

  const requestLink = (): void => {
    setProblem(null);
    setAttempt((previous) => previous + 1);
  };

  const close = useCallback(() => {
    setViewing(false);
  }, []);

  if (problem) {
    return (
      <div role="alert">
        <p className="list-error">
          {t(problem === 'rateLimited' ? 'kyc.documents.rateLimited' : 'kyc.documents.failed')}
        </p>
        <button type="button" className="button" onClick={requestLink}>
          {t('kyc.documents.reload')}
        </button>
      </div>
    );
  }
  if (!link) {
    return (
      <p role="status" className="muted">
        {t('kyc.documents.loading')}
      </p>
    );
  }

  return (
    <>
      <button
        ref={thumbnail}
        type="button"
        className="kyc-thumbnail"
        aria-label={t('kyc.documents.open', { document: label })}
        onClick={() => {
          if (Date.now() < link.expiresAt - linkMarginMs) {
            setViewing(true);
            return;
          }
          // An expired link would open a broken image: the thumbnail stays while a fresh one comes.
          // Further clicks meanwhile wait for the same one: every link is a view in the audit log.
          if (openOnLink.current) return;
          openOnLink.current = true;
          requestLink();
        }}
      >
        <img
          src={link.url}
          alt={label}
          referrerPolicy="no-referrer"
          onError={() => {
            setLink(null);
            setProblem('failed');
          }}
        />
      </button>
      {viewing && (
        <PhotoViewer url={link.url} label={label} onClose={close} returnFocusTo={thumbnail} />
      )}
    </>
  );
}

/**
 * The photo over the page, to read small print: zoom, rotation for a sideways shot, Escape closes.
 * The rest of the page is inert meanwhile, so Tab cannot reach the decision forms under it.
 */
function PhotoViewer({
  url,
  label,
  onClose,
  returnFocusTo,
}: {
  url: string;
  label: string;
  onClose: () => void;
  returnFocusTo: RefObject<HTMLElement | null>;
}) {
  const { t, i18n } = useTranslation();
  const [zoom, setZoom] = useState(minZoom);
  const [rotation, setRotation] = useState(0);
  // Width to height; a guess until the photo loads, phone photos are mostly 4:3.
  const [ratio, setRatio] = useState(4 / 3);
  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const layout = viewerLayout(ratio, zoom, rotation);

  useEffect(() => {
    const host = dialog.current;
    const opener = returnFocusTo.current;
    const others = [...document.body.children].filter(
      (element) => element !== host && !element.hasAttribute('inert'),
    );
    for (const element of others) element.setAttribute('inert', '');
    closeButton.current?.focus();
    return () => {
      for (const element of others) element.removeAttribute('inert');
      // Only now: an inert element does not take focus.
      opener?.focus();
    };
  }, [returnFocusTo]);

  // On the document, not on the dialog: focus can drop to the body, e.g. after a click on the photo.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      className="viewer"
      // A click on the photo focuses the dialog itself rather than dropping focus to the body.
      tabIndex={-1}
    >
      <div className="viewer-controls">
        <button
          type="button"
          className="button"
          // aria-disabled, not disabled: a disabled button loses focus, and the next key with it.
          aria-disabled={zoom <= minZoom}
          onClick={() => {
            setZoom((previous) => Math.max(minZoom, previous - zoomStep));
          }}
        >
          {t('kyc.viewer.zoomOut')}
        </button>
        <output aria-live="polite">
          {new Intl.NumberFormat(i18n.language, { style: 'percent' }).format(zoom)}
        </output>
        <button
          type="button"
          className="button"
          aria-disabled={zoom >= maxZoom}
          onClick={() => {
            setZoom((previous) => Math.min(maxZoom, previous + zoomStep));
          }}
        >
          {t('kyc.viewer.zoomIn')}
        </button>
        <button
          type="button"
          className="button"
          onClick={() => {
            setRotation((previous) => (previous + 90) % 360);
          }}
        >
          {t('kyc.viewer.rotate')}
        </button>
        <button ref={closeButton} type="button" className="button" onClick={onClose}>
          {t('kyc.viewer.close')}
        </button>
      </div>
      <div className="viewer-stage">
        <div className="viewer-frame" style={layout.frame}>
          <img
            src={url}
            alt={label}
            referrerPolicy="no-referrer"
            style={layout.image}
            onLoad={(event) => {
              const { naturalWidth, naturalHeight } = event.currentTarget;
              if (naturalWidth > 0 && naturalHeight > 0) setRatio(naturalWidth / naturalHeight);
            }}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}

type DecisionError =
  | 'dateRequired'
  | 'reasonRequired'
  | 'reasonTooLong'
  | 'licenseDateInvalid'
  | 'requirementsNotMet'
  | 'profileIncomplete'
  | 'invalid'
  | 'forbidden'
  | 'failed';

/**
 * What the operator is told about a refused decision. `notPending` reloads the application, since
 * someone else decided it; a vanished application lands on the same reload and shows as not found.
 */
function decisionError(error: unknown): DecisionError | 'notPending' | 'signOut' {
  if (!(error instanceof ApiError)) return 'failed';
  if (error.status === 401) return 'signOut';
  if (error.status === 403) return 'forbidden';
  switch (error.problem) {
    case 'kyc-not-pending':
    case 'kyc-application-not-found':
      return 'notPending';
    case 'kyc-requirements-not-met':
      return 'requirementsNotMet';
    case 'kyc-profile-incomplete':
      return 'profileIncomplete';
    case 'license-issue-date-invalid':
      return 'licenseDateInvalid';
    default:
      return error.status === 422 ? 'invalid' : 'failed';
  }
}

/**
 * Today as the API counts it, by the UTC date (ADR-0012): it refuses a license issued after that.
 * By Minsk's date the field would offer a day the API still calls the future until 03:00.
 */
const apiToday = (): string => new Date().toISOString().slice(0, 'YYYY-MM-DD'.length);

function Decision({
  application,
  onNotPending,
}: {
  application: KycApplication;
  onNotPending: () => void;
}) {
  const { t } = useTranslation();
  const { kycReview } = useApis();
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const [licenseIssuedOn, setLicenseIssuedOn] = useState('');
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<'approve' | 'reject' | null>(null);
  const [approveError, setApproveError] = useState<DecisionError | null>(null);
  const [rejectError, setRejectError] = useState<DecisionError | null>(null);

  const decide = (
    decision: 'approve' | 'reject',
    call: () => Promise<KycApplication>,
    setError: (error: DecisionError | null) => void,
  ): void => {
    setApproveError(null);
    setRejectError(null);
    setPending(decision);
    call().then(
      () => {
        const state: KycQueueState = { decided: decision === 'approve' ? 'approved' : 'rejected' };
        void navigate('/kyc', { state });
      },
      (error: unknown) => {
        setPending(null);
        const outcome = decisionError(error);
        if (outcome === 'signOut') void signOut();
        else if (outcome === 'notPending') onNotPending();
        else setError(outcome);
      },
    );
  };

  const approve = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>): void => {
    event.preventDefault();
    if (licenseIssuedOn === '') {
      setApproveError('dateRequired');
      return;
    }
    decide('approve', () => kycReview.approve(application.id, licenseIssuedOn), setApproveError);
  };

  const reject = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>): void => {
    event.preventDefault();
    const length = reasonLength(reason);
    if (length === 0) {
      setRejectError('reasonRequired');
      return;
    }
    if (length > rejectionReasonMaxLength) {
      setRejectError('reasonTooLong');
      return;
    }
    decide('reject', () => kycReview.reject(application.id, reason.trim()), setRejectError);
  };

  const errorText = (error: DecisionError): string =>
    t(`kyc.errors.${error}`, { max: rejectionReasonMaxLength });

  return (
    <div className="kyc-decision">
      <form
        className="card decision-form"
        noValidate
        aria-labelledby="kyc-approve-title"
        onSubmit={approve}
      >
        <h2 id="kyc-approve-title">{t('kyc.approve.title')}</h2>
        <label className="stacked-label">
          {t('kyc.approve.licenseIssuedOn')}
          <input
            className="field"
            type="date"
            value={licenseIssuedOn}
            min={application.birth_date ?? undefined}
            max={apiToday()}
            aria-invalid={approveError === 'dateRequired' || approveError === 'licenseDateInvalid'}
            onChange={(event) => {
              setLicenseIssuedOn(event.target.value);
            }}
          />
        </label>
        <p className="muted">{t('kyc.approve.hint')}</p>
        {approveError && (
          <p role="alert" className="login-error">
            {errorText(approveError)}
          </p>
        )}
        <button type="submit" className="button button-primary" disabled={pending !== null}>
          {pending === 'approve' ? t('kyc.pending') : t('kyc.approve.submit')}
        </button>
      </form>
      <form
        className="card decision-form"
        noValidate
        aria-labelledby="kyc-reject-title"
        onSubmit={reject}
      >
        <h2 id="kyc-reject-title">{t('kyc.reject.title')}</h2>
        <label className="stacked-label">
          {t('kyc.reject.reason')}
          <textarea
            className="field textarea"
            rows={4}
            value={reason}
            aria-invalid={rejectError === 'reasonRequired' || rejectError === 'reasonTooLong'}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </label>
        <p className="muted">
          {t('kyc.reject.length', { length: reasonLength(reason), max: rejectionReasonMaxLength })}
        </p>
        <p className="muted">{t('kyc.reject.hint')}</p>
        {rejectError && (
          <p role="alert" className="login-error">
            {errorText(rejectError)}
          </p>
        )}
        <button type="submit" className="button" disabled={pending !== null}>
          {pending === 'reject' ? t('kyc.pending') : t('kyc.reject.submit')}
        </button>
      </form>
    </div>
  );
}
