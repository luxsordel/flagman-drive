import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '../api/client';
import { useAuth } from '../auth/context';
import type { PhotoUrl } from './api';

/**
 * Asks for the link on a click, not on render: every link is an audit record, and a chat with
 * photos is polled and redrawn all the time. The link opens the photo without a token, so it
 * stays in this component.
 */
export function SupportPhoto({ load }: { load: () => Promise<PhotoUrl> }) {
  const { t } = useTranslation();
  const { signOut } = useAuth();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  if (url) return <img className="support-photo" src={url} alt={t('support.photo')} />;
  return (
    <>
      <button
        type="button"
        className="link-button"
        onClick={() => {
          setFailed(false);
          load().then(
            (photo) => {
              setUrl(photo.url);
            },
            (error: unknown) => {
              if (error instanceof ApiError && error.status === 401) void signOut();
              else setFailed(true);
            },
          );
        }}
      >
        {t('support.showPhoto')}
      </button>
      {failed && (
        <span role="alert" className="list-error">
          {' '}
          {t('support.photoFailed')}
        </span>
      )}
    </>
  );
}
