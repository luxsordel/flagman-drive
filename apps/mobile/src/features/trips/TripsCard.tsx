import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';

/** The way into the client's trip history from the profile. */
export function TripsCard() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Card>
      <AppText variant="label">{t('trips.title')}</AppText>
      <AppText tone="muted">{t('trips.cardHint')}</AppText>
      <Button
        variant="secondary"
        title={t('trips.open')}
        onPress={() => {
          router.push('/trips');
        }}
      />
    </Card>
  );
}
