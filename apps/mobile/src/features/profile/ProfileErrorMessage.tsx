import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import type { ProfileError } from './profileErrors';

export function ProfileErrorMessage({ error }: { error: ProfileError | null }) {
  const { t } = useTranslation();
  if (error === null) return null;
  return (
    <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
      {t(`profile.errors.${error.kind}`)}
    </AppText>
  );
}
