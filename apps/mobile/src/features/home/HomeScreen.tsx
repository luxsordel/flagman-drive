import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { useAuthSession } from '../auth/session/AuthProvider';
import { KycErrorMessage } from '../kyc/KycErrorMessage';
import { useKycApplication } from '../kyc/KycProvider';

/** Placeholder for signed-in clients until the map and profile screens arrive (#37). */
export function HomeScreen() {
  const { t } = useTranslation();
  const session = useAuthSession();
  const [busy, setBusy] = useState(false);

  return (
    <Screen>
      <AppText variant="title">{t('home.title')}</AppText>
      <AppText tone="muted">{t('home.subtitle')}</AppText>
      <DocumentsCard />
      <Button
        variant="secondary"
        title={t('auth.signOut')}
        disabled={busy}
        onPress={() => {
          setBusy(true);
          void session.signOut();
        }}
      />
    </Screen>
  );
}

function DocumentsCard() {
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
