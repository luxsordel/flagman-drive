import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

export function ForbiddenPage() {
  const { t } = useTranslation();

  return (
    <>
      <h1>{t('forbidden.title')}</h1>
      <p className="muted">{t('forbidden.text')}</p>
      <Link to="/">{t('forbidden.back')}</Link>
    </>
  );
}
