import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';

/** The way into the notification settings from the profile. */
export function NotificationsCard() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Card>
      <AppText variant="label">{t('notifications.title')}</AppText>
      <AppText tone="muted">{t('notifications.cardHint')}</AppText>
      <Button
        variant="secondary"
        title={t('notifications.open')}
        onPress={() => {
          router.push('/notification-settings');
        }}
      />
    </Card>
  );
}
