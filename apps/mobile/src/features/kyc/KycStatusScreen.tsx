import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import type { KycApplication } from './kycApi';
import { KycErrorMessage } from './KycErrorMessage';
import { useKycApplication, useKycStore } from './KycProvider';

/** Where the documents stand: not sent, under review, approved, or rejected with the reason. */
export function KycStatusScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { colors } = useTheme();
  const store = useKycStore();
  const { application, loading, loadError } = useKycApplication();

  return (
    <Screen>
      <AppText variant="title">{t('kyc.status.title')}</AppText>
      {application === null ? (
        loadError === null && <ActivityIndicator color={colors.primary} />
      ) : (
        <StatusCard application={application} />
      )}
      <KycErrorMessage error={loadError} />
      {application !== null &&
        (application.status === 'not_submitted' || application.status === 'rejected') && (
          <Button
            title={t(
              application.status === 'rejected' ? 'kyc.status.resubmit' : 'kyc.status.start',
            )}
            onPress={() => {
              router.push('/kyc/upload');
            }}
          />
        )}
      {(loadError !== null || application?.status === 'pending') && (
        <Button
          variant="secondary"
          title={t('kyc.status.refresh')}
          disabled={loading}
          onPress={() => void store.load()}
        />
      )}
      <Button
        variant="secondary"
        title={t('kyc.status.home')}
        onPress={() => {
          router.dismissTo('/');
        }}
      />
    </Screen>
  );
}

function StatusCard({ application }: { application: KycApplication }) {
  const { t, i18n } = useTranslation();
  const { status, rejection_reason: reason, submitted_at: submittedAt } = application;
  return (
    <Card>
      <AppText variant="label">{t(`kyc.statusShort.${status}`)}</AppText>
      <AppText>{t(`kyc.status.${status}`)}</AppText>
      {status === 'pending' && submittedAt !== null && (
        <AppText variant="caption" tone="muted">
          {t('kyc.status.submittedAt', { date: formatDate(submittedAt, i18n.language) })}
        </AppText>
      )}
      {status === 'rejected' && reason !== null && (
        <AppText tone="danger">{t('kyc.status.reason', { reason })}</AppText>
      )}
    </Card>
  );
}

function formatDate(iso: string, language: string): string {
  return new Intl.DateTimeFormat(language, { dateStyle: 'long', timeStyle: 'short' }).format(
    new Date(iso),
  );
}
