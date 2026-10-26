import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';

/** The way into the client's cards from the profile. */
export function PaymentMethodsCard() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Card>
      <AppText variant="label">{t('payments.title')}</AppText>
      <AppText tone="muted">{t('payments.cardHint')}</AppText>
      <Button
        variant="secondary"
        title={t('payments.open')}
        onPress={() => {
          router.push('/payment-methods');
        }}
      />
    </Card>
  );
}
