import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { KycErrorMessage } from './KycErrorMessage';
import { useKycApplication } from './KycProvider';

/** Short document status with a way into the documents, for screens outside the KYC flow. */
export function DocumentsCard() {
  const { t } = useTranslation();
  const router = useRouter();
  const { application, loadError } = useKycApplication();
  return (
    <Card>
      <AppText variant="label">{t('kyc.card.title')}</AppText>
      {application === null && loadError !== null ? (
        <KycErrorMessage error={loadError} />
      ) : (
        <AppText tone="muted">
          {application === null
            ? t('kyc.card.loading')
            : t(`kyc.statusShort.${application.status}`)}
        </AppText>
      )}
      <Button
        variant="secondary"
        title={t('kyc.card.open')}
        onPress={() => {
          router.push('/kyc');
        }}
      />
    </Card>
  );
}
