import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { formatWait } from '../auth/countdown';
import type { RentalError } from './rentalErrors';

export function RentalErrorMessage({ error }: { error: RentalError | null }) {
  const { t } = useTranslation();
  if (error === null) return null;
  const text =
    error.kind === 'rateLimited' && error.retryAfter !== undefined
      ? t('rental.errors.rateLimitedFor', { time: formatWait(error.retryAfter) })
      : t(`rental.errors.${error.kind}`);
  return (
    <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
      {text}
    </AppText>
  );
}
