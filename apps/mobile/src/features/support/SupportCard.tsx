import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';

/** The way into the support chat from the profile. */
export function SupportCard() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Card>
      <AppText variant="label">{t('support.title')}</AppText>
      <AppText tone="muted">{t('support.cardHint')}</AppText>
      <Button
        variant="secondary"
        title={t('support.open')}
        onPress={() => {
          router.push('/support');
        }}
      />
    </Card>
  );
}
