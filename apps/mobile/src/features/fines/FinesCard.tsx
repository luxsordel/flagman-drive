import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';

/** The way into the client's fines from the profile. */
export function FinesCard() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Card>
      <AppText variant="label">{t('fines.title')}</AppText>
      <AppText tone="muted">{t('fines.cardHint')}</AppText>
      <Button
        variant="secondary"
        title={t('fines.open')}
        onPress={() => {
          router.push('/fines');
        }}
      />
    </Card>
  );
}
