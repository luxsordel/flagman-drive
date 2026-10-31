import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { ReceiptView } from './ReceiptView';
import { RentalErrorMessage } from './RentalErrorMessage';
import { useRentalState, useRentalStore } from './RentalProvider';
import { endedText } from './rentalText';

/** The receipt of one rental by its id: where a tapped push about an ended rental leads. */
export function ReceiptScreen() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const router = useRouter();
  const store = useRentalStore();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { rental, viewed, pending, error } = useRentalState();

  useEffect(() => {
    if (id) void store.open(id);
  }, [store, id]);

  const toMap = () => {
    store.dismiss();
    router.dismissTo('/');
  };
  const shown = [viewed, rental].find((candidate) => candidate?.id === id) ?? null;
  // A reservation that expired or was cancelled in its free wait has no receipt.
  const title =
    shown !== null && shown.receipt === null ? 'rental.ended.title' : 'rental.receipt.title';
  return (
    <Screen>
      <AppText variant="title">{t(title)}</AppText>
      {shown === null ? (
        error === null ? (
          <>
            <ActivityIndicator color={colors.primary} />
            <AppText tone="muted">{t('rental.receipt.loading')}</AppText>
          </>
        ) : (
          <RentalErrorMessage error={error} />
        )
      ) : (
        <>
          {/* A cancelled reservation may have a receipt for its paid wait or none at all. */}
          {shown.status === 'cancelled' && <AppText>{t(endedText(shown))}</AppText>}
          <ReceiptView rental={shown} pending={pending} />
          <RentalErrorMessage error={error} />
        </>
      )}
      <Button variant="secondary" title={t('rental.toMap')} onPress={toMap} />
    </Screen>
  );
}
