import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '../api/client';
import { useApis } from '../api/context';
import { useAuth } from '../auth/context';
import { fullName } from '../kyc/format';
import { clientSearchMinLength, type ClientSummary } from './api';

// A pause in typing, so a phone number typed digit by digit is one search, not ten.
const SEARCH_DELAY_MS = 300;

/** Finds a client by phone or name for a filter; the first page of matches is enough to pick. */
export function ClientPicker({ onPick }: { onPick: (client: ClientSummary) => void }) {
  const { t } = useTranslation();
  const { clients } = useApis();
  const { signOut } = useAuth();
  const [search, setSearch] = useState('');
  const [found, setFound] = useState<ClientSummary[]>([]);
  const [failed, setFailed] = useState(false);
  const query = search.trim();
  // Matches of a longer search are not shown once it is cleared below the minimum.
  const shown = query.length >= clientSearchMinLength ? found : [];

  useEffect(() => {
    if (query.length < clientSearchMinLength) return;
    // An answer to an earlier search is dropped: it would replace the matches of this one.
    let active = true;
    const timer = setTimeout(() => {
      clients.list({ search: query }).then(
        (page) => {
          if (!active) return;
          setFound(page.items);
          setFailed(false);
        },
        (error: unknown) => {
          if (!active) return;
          if (error instanceof ApiError && error.status === 401) void signOut();
          else setFailed(true);
        },
      );
    }, SEARCH_DELAY_MS);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [clients, query, signOut]);

  return (
    <div>
      <label className="stacked-label">
        {t('fines.client')}
        <input
          className="field"
          type="search"
          placeholder={t('clients.search')}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
      </label>
      {failed && (
        <p role="alert" className="list-error">
          {t('fines.problems.failed')}
        </p>
      )}
      {shown.length > 0 && (
        <ul className="client-matches" aria-label={t('fines.clientMatches')}>
          {shown.map((client) => (
            <li key={client.id}>
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  setSearch('');
                  onPick(client);
                }}
              >
                {fullName(client) ?? t('support.noName')} · {client.phone}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
