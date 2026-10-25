import { useCallback, useEffect, useReducer, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth, useStaff } from '../auth/context';
import { formatMinskTime } from '../time/minsk';
import type { Zone, ZoneKind } from '../zones/api';
import {
  draftProblem,
  editorReducer,
  initialEditorState,
  withSavedZone,
  type EditorAction,
  type EditorState,
} from '../zones/editorState';
import { maxZoneVertices, zoneInput, type Ring } from '../zones/geometry';
import { zoneError, type ZoneError } from '../zones/problems';
import { ZoneMap } from '../zones/ZoneMap';

const zoneKinds: readonly ZoneKind[] = ['allowed', 'forbidden'];

type ListState =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'forbidden' }
  | { status: 'ready'; zones: Zone[] };

/**
 * Zones where rentals may end, on the map. Every staff member sees them; only an admin draws,
 * changes and disables them, and the API checks that on every call.
 */
export function ZonesPage() {
  const { t } = useTranslation();
  const { zones: zonesApi } = useApis();
  const { signOut } = useAuth();
  const canEdit = useStaff().role === 'admin';
  const [list, setList] = useState<ListState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [editor, dispatch] = useReducer(editorReducer, initialEditorState);

  useEffect(() => {
    let active = true;
    zonesApi.list().then(
      (zones) => {
        if (active) setList({ status: 'ready', zones });
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
  }, [zonesApi, attempt, signOut]);

  const select = useCallback((id: string | null) => {
    dispatch({ type: 'select', id });
  }, []);
  const setRing = useCallback((ring: Ring) => {
    dispatch({ type: 'setRing', ring });
  }, []);

  const replaceZones = (change: (zones: Zone[]) => Zone[]): void => {
    setList((previous) =>
      previous.status === 'ready' ? { status: 'ready', zones: change(previous.zones) } : previous,
    );
  };

  if (list.status === 'loading') {
    return (
      <>
        <h1>{t('nav.zones')}</h1>
        <p role="status" className="muted">
          {t('zones.loading')}
        </p>
      </>
    );
  }
  if (list.status === 'forbidden') {
    return (
      <>
        <h1>{t('nav.zones')}</h1>
        <p role="alert" className="list-error">
          {t('forbidden.text')}
        </p>
      </>
    );
  }
  if (list.status === 'failed') {
    return (
      <>
        <h1>{t('nav.zones')}</h1>
        <div role="alert" className="card list-alert">
          <p>{t('zones.failed')}</p>
          <button
            type="button"
            className="button"
            onClick={() => {
              setList({ status: 'loading' });
              setAttempt((previous) => previous + 1);
            }}
          >
            {t('zones.retry')}
          </button>
        </div>
      </>
    );
  }

  const { zones } = list;
  const selectedId = editor.mode === 'view' ? editor.selectedId : editor.zoneId;
  const selected = zones.find((zone) => zone.id === selectedId) ?? null;

  return (
    <>
      <h1>{t('nav.zones')}</h1>
      <div className="zones-layout">
        <ZoneMap
          zones={zones}
          selectedId={selectedId}
          editing={
            editor.mode === 'view'
              ? null
              : { zoneId: editor.zoneId, kind: editor.draft.kind, ring: editor.draft.ring }
          }
          onSelect={select}
          onRingChange={setRing}
          label={t('zones.map')}
        />
        <aside className="zones-panel">
          {editor.mode === 'view' ? (
            <ZoneList
              zones={zones}
              selected={selected}
              canEdit={canEdit}
              onSelect={select}
              onCreate={() => {
                dispatch({ type: 'startCreate' });
              }}
              onEdit={(zone) => {
                dispatch({ type: 'startEdit', zone });
              }}
              onDisabled={(zone) => {
                replaceZones((current) => current.filter((candidate) => candidate.id !== zone.id));
                dispatch({ type: 'select', id: null });
              }}
            />
          ) : (
            <ZoneForm
              editor={editor}
              dispatch={dispatch}
              onSaved={(zone) => {
                replaceZones((current) => withSavedZone(current, zone));
              }}
            />
          )}
        </aside>
      </div>
    </>
  );
}

function ZoneList({
  zones,
  selected,
  canEdit,
  onSelect,
  onCreate,
  onEdit,
  onDisabled,
}: {
  zones: readonly Zone[];
  selected: Zone | null;
  canEdit: boolean;
  onSelect: (id: string | null) => void;
  onCreate: () => void;
  onEdit: (zone: Zone) => void;
  onDisabled: (zone: Zone) => void;
}) {
  const { t } = useTranslation();

  return (
    <>
      {canEdit && (
        <button type="button" className="button button-primary" onClick={onCreate}>
          {t('zones.create')}
        </button>
      )}
      {zones.length === 0 ? (
        <p className="muted">{t('zones.empty')}</p>
      ) : (
        <ul className="zones-list" aria-label={t('zones.list')}>
          {zones.map((zone) => (
            <li key={zone.id}>
              <button
                type="button"
                className="zones-item"
                aria-pressed={zone.id === selected?.id}
                onClick={() => {
                  onSelect(zone.id === selected?.id ? null : zone.id);
                }}
              >
                <span className={`zones-swatch zones-swatch-${zone.kind}`} aria-hidden="true" />
                <span>{zone.name}</span>
                <span className="muted">{t(`zones.kind.${zone.kind}`)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {selected && (
        // Keyed by zone: a confirmation started for one zone never applies to another.
        <SelectedZone
          key={selected.id}
          zone={selected}
          canEdit={canEdit}
          onEdit={onEdit}
          onDisabled={onDisabled}
        />
      )}
    </>
  );
}

function SelectedZone({
  zone,
  canEdit,
  onEdit,
  onDisabled,
}: {
  zone: Zone;
  canEdit: boolean;
  onEdit: (zone: Zone) => void;
  onDisabled: (zone: Zone) => void;
}) {
  const { t, i18n } = useTranslation();
  const { zones: zonesApi } = useApis();
  const { signOut } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [error, setError] = useState<ZoneError | null>(null);

  const disable = (): void => {
    setDisabling(true);
    setError(null);
    zonesApi.disable(zone.id).then(
      () => {
        onDisabled(zone);
      },
      (failure: unknown) => {
        setDisabling(false);
        const outcome = zoneError(failure);
        if (outcome === 'signOut') void signOut();
        else setError(outcome);
      },
    );
  };

  return (
    <section className="card zones-selected" aria-labelledby="zones-selected-title">
      <h2 id="zones-selected-title">{zone.name}</h2>
      <dl className="details">
        <dt>{t('zones.kindLabel')}</dt>
        <dd>{t(`zones.kind.${zone.kind}`)}</dd>
        <dt>{t('zones.updated')}</dt>
        <dd>
          <time dateTime={zone.updated_at}>{formatMinskTime(zone.updated_at, i18n.language)}</time>
        </dd>
      </dl>
      {error && (
        <p role="alert" className="list-error">
          {t(`zones.errors.${error}`)}
        </p>
      )}
      {canEdit &&
        (confirming ? (
          <div role="group" aria-label={t('zones.disableConfirm.title')} className="zones-confirm">
            <p>{t('zones.disableConfirm.text', { name: zone.name })}</p>
            <div className="zones-actions">
              <button
                type="button"
                className="button button-danger"
                disabled={disabling}
                onClick={disable}
              >
                {disabling ? t('zones.disabling') : t('zones.disableConfirm.confirm')}
              </button>
              <button
                type="button"
                className="button"
                disabled={disabling}
                onClick={() => {
                  setConfirming(false);
                }}
              >
                {t('zones.disableConfirm.keep')}
              </button>
            </div>
          </div>
        ) : (
          <div className="zones-actions">
            <button
              type="button"
              className="button"
              onClick={() => {
                onEdit(zone);
              }}
            >
              {t('zones.edit')}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirming(true);
              }}
            >
              {t('zones.disable')}
            </button>
          </div>
        ))}
    </section>
  );
}

function ZoneForm({
  editor,
  dispatch,
  onSaved,
}: {
  editor: Exclude<EditorState, { mode: 'view' }>;
  dispatch: (action: EditorAction) => void;
  onSaved: (zone: Zone) => void;
}) {
  const { t } = useTranslation();
  const { zones: zonesApi } = useApis();
  const { signOut } = useAuth();
  const [submitted, setSubmitted] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const { draft, saving, error } = editor;
  const problem = draftProblem(draft);
  const creating = editor.mode === 'create';

  const save = (): void => {
    setSubmitted(true);
    if (problem) return;
    dispatch({ type: 'saveStarted' });
    const input = zoneInput(draft.name, draft.kind, draft.ring);
    const request = editor.zoneId ? zonesApi.update(editor.zoneId, input) : zonesApi.create(input);
    request.then(
      (zone) => {
        onSaved(zone);
        dispatch({ type: 'saved', zone });
      },
      (failure: unknown) => {
        const outcome = zoneError(failure);
        if (outcome === 'signOut') {
          void signOut();
          return;
        }
        dispatch({ type: 'saveFailed', error: outcome });
      },
    );
  };

  const cancel = (): void => {
    if (editor.dirty && !confirmingCancel) {
      setConfirmingCancel(true);
      return;
    }
    dispatch({ type: 'cancel' });
  };

  return (
    <form
      className="card zones-form"
      aria-labelledby="zones-form-title"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <h2 id="zones-form-title">
        {t(creating ? 'zones.form.createTitle' : 'zones.form.editTitle')}
      </h2>
      <p className="muted">
        {t(creating && draft.ring.length === 0 ? 'zones.form.drawHint' : 'zones.form.editHint')}
      </p>
      <label className="stacked-label">
        {t('zones.form.name')}
        <input
          className="field"
          value={draft.name}
          disabled={saving}
          onChange={(event) => {
            dispatch({ type: 'setName', name: event.target.value });
          }}
        />
      </label>
      <fieldset className="zones-kinds" disabled={saving}>
        <legend>{t('zones.kindLabel')}</legend>
        {zoneKinds.map((kind) => (
          <label key={kind} className="inline-label">
            <input
              type="radio"
              name="zone-kind"
              value={kind}
              checked={draft.kind === kind}
              onChange={() => {
                dispatch({ type: 'setKind', kind });
              }}
            />
            {t(`zones.kind.${kind}`)}
          </label>
        ))}
      </fieldset>
      <p className="muted">
        {t('zones.form.vertices', { count: draft.ring.length, max: maxZoneVertices })}
      </p>
      {submitted && problem && (
        <p role="alert" className="list-error">
          {t(`zones.form.problems.${problem}`, { max: maxZoneVertices })}
        </p>
      )}
      {error && (
        <p role="alert" className="list-error">
          {t(`zones.errors.${error}`)}
        </p>
      )}
      {confirmingCancel ? (
        <div role="group" aria-label={t('zones.cancelConfirm.title')} className="zones-confirm">
          <p>{t('zones.cancelConfirm.text')}</p>
          <div className="zones-actions">
            <button type="button" className="button button-danger" onClick={cancel}>
              {t('zones.cancelConfirm.discard')}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirmingCancel(false);
              }}
            >
              {t('zones.cancelConfirm.keep')}
            </button>
          </div>
        </div>
      ) : (
        <div className="zones-actions">
          <button type="submit" className="button button-primary" disabled={saving}>
            {saving ? t('zones.form.saving') : t('zones.form.save')}
          </button>
          <button type="button" className="button" disabled={saving} onClick={cancel}>
            {t('zones.form.cancel')}
          </button>
        </div>
      )}
    </form>
  );
}
