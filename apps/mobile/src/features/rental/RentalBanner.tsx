import { radius, spacing } from '@flagman/design-tokens';
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { formatKopecks } from '../map/mapFormat';
import type { Rental } from './rentalApi';
import { useRentalState, useRentalStore } from './RentalProvider';
import { ReservationCountdown } from './ReservationCountdown';
import { endedText } from './rentalText';
import { isActive } from './rentalTime';

/** Takes the car card's place on the map while the client has a rental: one car at a time. */
export function RentalBanner({
  rental,
  onOpen,
  onReceipt,
}: {
  rental: Rental;
  onOpen: () => void;
  onReceipt: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const { colors } = useTheme();
  const store = useRentalStore();
  const { offsetMs } = useRentalState();
  // Stable, so the countdown asks the server once when the time is over, not on every tick.
  const refresh = useCallback(() => void store.refresh(), [store]);
  const car = `${rental.car.brand} ${rental.car.model}`;

  return (
    <View style={[styles.sheet, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      {isActive(rental) ? (
        <>
          <AppText variant="label">
            {rental.status === 'reserved' ? t('rental.banner.reserved') : t('rental.banner.inTrip')}
          </AppText>
          <AppText variant="title">{car}</AppText>
          {rental.status === 'reserved' ? (
            <ReservationCountdown rental={rental} offsetMs={offsetMs} onOver={refresh} />
          ) : null}
          <Button title={t('rental.banner.open')} onPress={onOpen} />
        </>
      ) : (
        <>
          <AppText>{t(endedText(rental))}</AppText>
          {/* The money of any ended rental: an expiry bills the paid wait too (ADR-0024), and a
              client who did not watch it may get no push, e.g. on the web. */}
          {rental.receipt !== null ? (
            <>
              <AppText variant="title">
                {t('rental.receipt.total', {
                  amount: formatKopecks(rental.receipt.cost.total, i18n.language),
                })}
              </AppText>
              <Button
                title={t('rental.banner.receipt')}
                onPress={() => {
                  onReceipt(rental.id);
                }}
              />
            </>
          ) : null}
          <Button
            variant="secondary"
            title={t('rental.toMap')}
            onPress={() => {
              store.dismiss();
            }}
          />
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  // Floats above the map's attribution like the car card it replaces.
  sheet: {
    borderWidth: 1,
    borderRadius: radius.sheet,
    marginHorizontal: spacing.lg,
    padding: spacing.xl,
    gap: spacing.md,
  },
});
