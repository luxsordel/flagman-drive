import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { formatKopecks, kopecksToRubles, parseRubles } from '../tariffs/money';
import { formatMinskTime } from '../time/minsk';
import {
  fineAmountMaxKopecks,
  fineCodePattern,
  fineNameMaxLength,
  type FineTerms,
  type FineType,
} from './api';
import { fineName } from './format';

type CatalogProblem = 'exists' | 'changed' | 'invalid' | 'failed';

function catalogProblem(error: unknown): CatalogProblem {
  if (error instanceof ApiError) {
    if (error.problem === 'fine-type-exists') return 'exists';
    if (error.problem === 'fine-type-changed') return 'changed';
    if (error.status === 422) return 'invalid';
  }
  return 'failed';
}

interface Draft {
  nameRu: string;
  nameEn: string;
  amount: string;
}

const emptyDraft: Draft = { nameRu: '', nameEn: '', amount: '' };

function readDraft(draft: Draft): FineTerms | null {
  const amount = parseRubles(draft.amount);
  const nameRu = draft.nameRu.trim();
  const nameEn = draft.nameEn.trim();
  if (amount === null || amount < 1 || amount > fineAmountMaxKopecks) return null;
  if (!nameRu || !nameEn) return null;
  return { name_ru: nameRu, name_en: nameEn, amount };
}

function DraftFields({ draft, onChange }: { draft: Draft; onChange: (draft: Draft) => void }) {
  const { t } = useTranslation();
  return (
    <>
      <label className="stacked-label">
        {t('fines.catalog.nameRu')}
        <input
          className="field"
          maxLength={fineNameMaxLength}
          value={draft.nameRu}
          onChange={(event) => {
            onChange({ ...draft, nameRu: event.target.value });
          }}
        />
      </label>
      <label className="stacked-label">
        {t('fines.catalog.nameEn')}
        <input
          className="field"
          maxLength={fineNameMaxLength}
          value={draft.nameEn}
          onChange={(event) => {
            onChange({ ...draft, nameEn: event.target.value });
          }}
        />
      </label>
      <label className="stacked-label">
        {t('fines.catalog.amount')}
        <input
          className="field"
          inputMode="decimal"
          value={draft.amount}
          onChange={(event) => {
            onChange({ ...draft, amount: event.target.value });
          }}
        />
      </label>
    </>
  );
}

/**
 * The fine catalog. Operators read it; an admin adds manual types and changes names and amounts.
 * A change is a new version: fines charged before keep their amount.
 */
export function FineCatalog({
  types,
  canEdit,
  onChanged,
}: {
  types: FineType[];
  canEdit: boolean;
  onChanged: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { fines } = useApis();
  const { signOut } = useAuth();
  const [editing, setEditing] = useState<FineType | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [code, setCode] = useState('');
  const [adding, setAdding] = useState<Draft>(emptyDraft);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<CatalogProblem | null>(null);

  const save = (action: () => Promise<FineType>, done: () => void) => {
    setBusy(true);
    setProblem(null);
    action()
      .then(
        () => {
          done();
          onChanged();
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 401) void signOut();
          else setProblem(catalogProblem(error));
        },
      )
      .finally(() => {
        setBusy(false);
      });
  };

  const edited = readDraft(draft);
  const added = readDraft(adding);
  const codeValid = fineCodePattern.test(code);

  return (
    <section>
      <h2>{t('fines.catalog.title')}</h2>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{t('fines.catalog.name')}</th>
              <th scope="col">{t('fines.catalog.code')}</th>
              <th scope="col">{t('fines.catalog.kind')}</th>
              <th scope="col">{t('fines.catalog.amount')}</th>
              <th scope="col">{t('fines.catalog.validFrom')}</th>
              {canEdit && <th scope="col" />}
            </tr>
          </thead>
          <tbody>
            {types.map((type) => (
              <tr key={type.fine_type_id}>
                <td>{fineName(type, i18n.language)}</td>
                <td>
                  <code>{type.code}</code>
                </td>
                <td>{t(`fines.kinds.${type.kind}`)}</td>
                <td>{formatKopecks(type.amount, i18n.language)}</td>
                <td>
                  <time dateTime={type.valid_from}>
                    {formatMinskTime(type.valid_from, i18n.language)}
                  </time>
                </td>
                {canEdit && (
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => {
                        setEditing(type);
                        setProblem(null);
                        setDraft({
                          nameRu: type.name_ru,
                          nameEn: type.name_en,
                          amount: kopecksToRubles(type.amount),
                        });
                      }}
                    >
                      {t('fines.catalog.edit')}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {problem && (
        <p role="alert" className="list-error">
          {t(`fines.catalog.problems.${problem}`)}
        </p>
      )}
      {canEdit && editing && (
        <form
          className="decision-form"
          aria-label={t('fines.catalog.editTitle', { name: fineName(editing, i18n.language) })}
          onSubmit={(event) => {
            event.preventDefault();
            if (!edited) return;
            if (!window.confirm(t('fines.catalog.editConfirm'))) return;
            save(
              () => fines.updateType(editing.fine_type_id, editing.number, edited),
              () => {
                setEditing(null);
              },
            );
          }}
        >
          <h3>{t('fines.catalog.editTitle', { name: fineName(editing, i18n.language) })}</h3>
          <div className="filters">
            <DraftFields draft={draft} onChange={setDraft} />
          </div>
          <button type="submit" className="button button-primary" disabled={busy || !edited}>
            {t('fines.catalog.save')}
          </button>
          <button
            type="button"
            className="button"
            onClick={() => {
              setEditing(null);
            }}
          >
            {t('fines.catalog.cancel')}
          </button>
        </form>
      )}
      {canEdit && (
        <form
          className="decision-form"
          aria-label={t('fines.catalog.addTitle')}
          onSubmit={(event) => {
            event.preventDefault();
            if (!added || !codeValid) return;
            save(
              () => fines.createType(code, added),
              () => {
                setCode('');
                setAdding(emptyDraft);
              },
            );
          }}
        >
          <h3>{t('fines.catalog.addTitle')}</h3>
          <p className="muted">{t('fines.catalog.addHint')}</p>
          <div className="filters">
            <label className="stacked-label">
              {t('fines.catalog.code')}
              <input
                className="field"
                maxLength={40}
                value={code}
                onChange={(event) => {
                  setCode(event.target.value);
                }}
              />
            </label>
            <DraftFields draft={adding} onChange={setAdding} />
          </div>
          <button
            type="submit"
            className="button button-primary"
            disabled={busy || !added || !codeValid}
          >
            {t('fines.catalog.add')}
          </button>
        </form>
      )}
    </section>
  );
}
