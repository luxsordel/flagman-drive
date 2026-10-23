import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { formatWait } from '../auth/countdown';
import type { KycError } from './kycErrors';

export function KycErrorMessage({ error }: { error: KycError | null }) {
  const { t } = useTranslation();
  if (error === null) return null;
  const text =
    error.kind === 'rateLimited' && error.retryAfter !== undefined
      ? t('kyc.errors.rateLimitedFor', { time: formatWait(error.retryAfter) })
      : t(`kyc.errors.${error.kind}`);
  return (
    <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
      {text}
    </AppText>
  );
}
