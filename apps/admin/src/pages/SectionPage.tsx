import { useTranslation } from 'react-i18next';

import type { SectionKey } from '../navigation';

/** Placeholder for a section that later tasks fill in. */
export function SectionPage({ section }: { section: SectionKey }) {
  const { t } = useTranslation();

  return (
    <>
      <h1>{t(`nav.${section}`)}</h1>
      <p className="muted">{t('section.placeholder')}</p>
    </>
  );
}
