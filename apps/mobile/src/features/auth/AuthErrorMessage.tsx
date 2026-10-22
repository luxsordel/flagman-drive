import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { formatWait } from './countdown';
import type { AuthError } from './session/authErrors';

export function AuthErrorMessage({ error }: { error: AuthError | null }) {
  const { t } = useTranslation();
  if (error === null) return null;
  const text =
    error.kind === 'rateLimited' && error.retryAfter !== undefined
      ? t('auth.errors.rateLimitedFor', { time: formatWait(error.retryAfter) })
      : t(`auth.errors.${error.kind}`);
  return (
    <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
      {text}
    </AppText>
  );
}
