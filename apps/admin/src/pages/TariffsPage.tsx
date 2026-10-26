import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth, useStaff } from '../auth/context';
import type { ModelTariff, TariffVersion } from '../tariffs/api';
import {
  draftOf,
  emptyDraft,
  fieldLimit,
  priceFields,
  readDraft,
  type FieldProblem,
  type PriceField,
  type TariffDraft,
} from '../tariffs/form';
import { formatKopecks } from '../tariffs/money';
import { staleErrors, tariffError, type TariffError } from '../tariffs/problems';
import { formatMinskTime } from '../time/minsk';

type ListState =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'forbidden' }
  | { status: 'ready'; items: ModelTariff[] };

/**
 * Tariffs of the car models. Operators and admins see them; only an admin changes them, and the
 * API checks that on every call. A change saves a new version: rentals booked before keep theirs.
 */
export function TariffsPage() {
  const { t } = useTranslation();
  const { tariffs } = useApis();
  const { signOut } = useAuth();
  const canEdit = useStaff().role === 'admin';
  const [list, setList] = useState<ListState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    tariffs.list().then(
      (items) => {
        if (active) setList({ status: 'ready', items });
      },
      (error: unknown) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) {
          void signOut();
          return;
        }
        const forbidden = error instanceof ApiError && error.status === 403;
        setList({ status: forbidden ? 'forbidden' : 'failed' });
      },
    );
    return () => {
      active = false;
    };
  }, [tariffs, attempt, signOut]);

  const reload = (): void => {
    setList({ status: 'loading' });
    setAttempt((previous) => previous + 1);
  };

  return (
    <>
      <h1>{t('tariffs.title')}</h1>
      {list.status === 'loading' && (
        <p role="status" className="muted">
          {t('tariffs.loading')}
        </p>
      )}
      {list.status === 'forbidden' && (
        <p role="alert" className="list-error">
          {t('forbidden.text')}
        </p>
      )}
      {list.status === 'failed' && (
        <div role="alert" className="card list-alert">
          <p>{t('tariffs.failed')}</p>
          <button type="button" className="button" onClick={reload}>
            {t('tariffs.retry')}
          </button>
        </div>
      )}
      {list.status === 'ready' && (
        <>
          <p className="muted">{t('tariffs.hint')}</p>
          {saved && (
            <p role="status" className="notice">
              {t('tariffs.saved', { model: saved })}
            </p>
          )}
          {list.items.length === 0 ? (
            <p className="muted">{t('tariffs.empty')}</p>
          ) : (
            <TariffTable
              items={list.items}
              selectedModel={selectedModel}
              onSelect={(modelId) => {
                setSaved(null);
                setSelectedModel(modelId === selectedModel ? null : modelId);
              }}
            />
          )}
          {list.items
            .filter((item) => item.model.id === selectedModel)
            .map((item) => (
              // Keyed by model and version: a form filled for one never saves over another.
              <TariffPanel
                key={`${item.model.id}-${String(item.current?.number ?? 0)}`}
                item={item}
                canEdit={canEdit}
                onSaved={(changed) => {
                  setList((previous) =>
                    previous.status === 'ready'
                      ? {
                          status: 'ready',
                          items: previous.items.map((existing) =>
                            existing.model.id === changed.model.id ? changed : existing,
                          ),
                        }
                      : previous,
                  );
                  setSaved(`${changed.model.brand} ${changed.model.name}`);
                  setSelectedModel(null);
                }}
                onStale={reload}
              />
            ))}
        </>
      )}
    </>
  );
}

function TariffTable({
  items,
  selectedModel,
  onSelect,
}: {
  items: ModelTariff[];
  selectedModel: string | null;
  onSelect: (modelId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">{t('tariffs.columns.model')}</th>
            {priceFields.map((field) => (
              <th key={field} scope="col">
                {t(`tariffs.fields.${field}`)}
              </th>
            ))}
            <th scope="col">{t('tariffs.columns.validFrom')}</th>
          </tr>
        </thead>
        <tbody>
          {items.map(({ model, current }) => (
            <tr key={model.id}>
              <th scope="row">
                <button
                  type="button"
                  className="link-button"
                  aria-expanded={model.id === selectedModel}
                  onClick={() => {
                    onSelect(model.id);
                  }}
                >
                  {`${model.brand} ${model.name}`}
                </button>
              </th>
              {current ? (
                <>
                  {priceFields.map((field) => (
                    <td key={field}>
                      {field === 'free_waiting_minutes'
                        ? t('tariffs.minutes', { count: current.prices[field] })
                        : formatKopecks(current.prices[field], i18n.language)}
                    </td>
                  ))}
                  <td>
                    <time dateTime={current.valid_from}>
                      {formatMinskTime(current.valid_from, i18n.language)}
                    </time>
                  </td>
                </>
              ) : (
                <td colSpan={priceFields.length + 1} className="muted">
                  {t('tariffs.none')}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TariffPanel({
  item,
  canEdit,
  onSaved,
  onStale,
}: {
  item: ModelTariff;
  canEdit: boolean;
  onSaved: (changed: ModelTariff) => void;
  onStale: () => void;
}) {
  const { t } = useTranslation();
  const title = `${item.model.brand} ${item.model.name}`;
  return (
    <section className="card tariffs-panel" aria-labelledby="tariffs-panel-title">
      <h2 id="tariffs-panel-title">{title}</h2>
      {canEdit ? (
        <TariffForm item={item} onSaved={onSaved} onStale={onStale} />
      ) : (
        !item.current && <p className="muted">{t('tariffs.noneReadOnly')}</p>
      )}
      {item.tariff_id && <VersionHistory tariffId={item.tariff_id} />}
    </section>
  );
}

function TariffForm({
  item,
  onSaved,
  onStale,
}: {
  item: ModelTariff;
  onSaved: (changed: ModelTariff) => void;
  onStale: () => void;
}) {
  const { t } = useTranslation();
  const { tariffs } = useApis();
  const { signOut } = useAuth();
  const [draft, setDraft] = useState<TariffDraft>(() =>
    item.current ? draftOf(item.current.prices) : emptyDraft(),
  );
  const [problems, setProblems] = useState<Partial<Record<PriceField, FieldProblem>>>({});
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<TariffError | null>(null);
  const creating = !item.current;

  const review = (): void => {
    const read = readDraft(draft);
    if ('problems' in read) {
      setProblems(read.problems);
      return;
    }
    setProblems({});
    setError(null);
    setConfirming(true);
  };

  const save = (): void => {
    const read = readDraft(draft);
    if ('problems' in read) return;
    setSaving(true);
    const request =
      item.tariff_id && item.current
        ? tariffs.update(item.tariff_id, item.current.number, read.prices)
        : tariffs.create(item.model.id, read.prices);
    request.then(onSaved, (failure: unknown) => {
      setSaving(false);
      setConfirming(false);
      const outcome = tariffError(failure);
      if (outcome === 'signOut') void signOut();
      else setError(outcome);
    });
  };

  return (
    <form
      className="tariffs-form"
      aria-labelledby="tariffs-form-title"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        review();
      }}
    >
      <h3 id="tariffs-form-title">
        {t(creating ? 'tariffs.form.createTitle' : 'tariffs.form.editTitle')}
      </h3>
      <fieldset className="tariffs-fields" disabled={saving || confirming}>
        {priceFields.map((field) => {
          const problem = problems[field];
          const id = `tariff-${field}`;
          return (
            <label key={field} className="stacked-label" htmlFor={id}>
              {t(`tariffs.fields.${field}`)}
              <span className="muted">
                {t(
                  field === 'free_waiting_minutes'
                    ? 'tariffs.form.inMinutes'
                    : 'tariffs.form.inRubles',
                )}
              </span>
              <input
                id={id}
                className="field"
                inputMode="decimal"
                value={draft[field]}
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? `${id}-problem` : undefined}
                onChange={(event) => {
                  setDraft((previous) => ({ ...previous, [field]: event.target.value }));
                }}
              />
              {problem && (
                <span id={`${id}-problem`} className="list-error">
                  {t(`tariffs.form.problems.${problem}`, { max: fieldLimit(field) })}
                </span>
              )}
            </label>
          );
        })}
      </fieldset>
      {error && (
        <div role="alert" className="list-error">
          <p>{t(`tariffs.errors.${error}`)}</p>
          {staleErrors.has(error) && (
            <button type="button" className="button" onClick={onStale}>
              {t('tariffs.reload')}
            </button>
          )}
        </div>
      )}
      {confirming ? (
        <div role="group" aria-label={t('tariffs.confirm.title')} className="tariffs-confirm">
          <p>{t(creating ? 'tariffs.confirm.createText' : 'tariffs.confirm.editText')}</p>
          <div className="tariffs-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={saving}
              onClick={save}
            >
              {saving ? t('tariffs.form.saving') : t('tariffs.confirm.confirm')}
            </button>
            <button
              type="button"
              className="button"
              disabled={saving}
              onClick={() => {
                setConfirming(false);
              }}
            >
              {t('tariffs.confirm.back')}
            </button>
          </div>
        </div>
      ) : (
        <div className="tariffs-actions">
          <button type="submit" className="button button-primary">
            {t('tariffs.form.save')}
          </button>
        </div>
      )}
    </form>
  );
}

type HistoryState =
  { status: 'loading' } | { status: 'failed' } | { status: 'ready'; versions: TariffVersion[] };

function VersionHistory({ tariffId }: { tariffId: string }) {
  const { t, i18n } = useTranslation();
  const { tariffs } = useApis();
  const { signOut } = useAuth();
  const [history, setHistory] = useState<HistoryState>({ status: 'loading' });

  useEffect(() => {
    let active = true;
    tariffs.versions(tariffId).then(
      (versions) => {
        if (active) setHistory({ status: 'ready', versions });
      },
      (error: unknown) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) void signOut();
        else setHistory({ status: 'failed' });
      },
    );
    return () => {
      active = false;
    };
  }, [tariffs, tariffId, signOut]);

  return (
    <section aria-labelledby="tariffs-history-title">
      <h3 id="tariffs-history-title">{t('tariffs.history.title')}</h3>
      {history.status === 'loading' && (
        <p role="status" className="muted">
          {t('tariffs.history.loading')}
        </p>
      )}
      {history.status === 'failed' && (
        <p role="alert" className="list-error">
          {t('tariffs.history.failed')}
        </p>
      )}
      {history.status === 'ready' && (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t('tariffs.history.version')}</th>
                <th scope="col">{t('tariffs.columns.validFrom')}</th>
                {priceFields.map((field) => (
                  <th key={field} scope="col">
                    {t(`tariffs.fields.${field}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.versions.map((version) => (
                <tr key={version.id}>
                  <td>{version.number}</td>
                  <td>
                    <time dateTime={version.valid_from}>
                      {formatMinskTime(version.valid_from, i18n.language)}
                    </time>
                  </td>
                  {priceFields.map((field) => (
                    <td key={field}>
                      {field === 'free_waiting_minutes'
                        ? t('tariffs.minutes', { count: version.prices[field] })
                        : formatKopecks(version.prices[field], i18n.language)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
