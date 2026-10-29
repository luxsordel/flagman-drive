import { radius, spacing } from '@flagman/design-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Image, StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import type { RentalError } from '../rental/rentalErrors';
import { RentalErrorMessage } from '../rental/RentalErrorMessage';
import { formatDistance, formatKopecks } from './mapFormat';
import type { SelectedCar } from './mapStore';

const photoHeight = 120;

export interface Reservation {
  /** Null while the documents are loading: the button waits rather than flashing a refusal. */
  kycApproved: boolean | null;
  reserving: boolean;
  /** Resolves with the reason the reservation was refused, or null once it is made. */
  onReserve: () => Promise<RentalError | null>;
  onOpenDocuments: () => void;
}

/** The bottom card of the car picked on the map, with the reservation of it. */
export function CarCard({
  selected,
  reservation,
  onClose,
}: {
  selected: SelectedCar;
  reservation: Reservation;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { colors } = useTheme();
  const { car, loading, error } = selected;
  const distance = car?.distance_m != null ? formatDistance(car.distance_m, i18n.language) : null;

  return (
    <View
      style={[styles.sheet, { backgroundColor: colors.surface, borderColor: colors.border }]}
      accessibilityViewIsModal
    >
      {car === null ? (
        loading ? (
          <ActivityIndicator color={colors.primary} />
        ) : null
      ) : (
        <>
          {car.photo_url !== null ? (
            <Image
              source={{ uri: car.photo_url }}
              style={styles.photo}
              resizeMode="contain"
              accessibilityLabel={t('map.card.photo', { model: `${car.brand} ${car.model}` })}
            />
          ) : null}
          <AppText variant="title">{`${car.brand} ${car.model}`}</AppText>
          <View style={styles.facts}>
            <Fact
              label={t('map.card.battery')}
              value={t('map.card.batteryValue', { percent: car.battery_pct })}
            />
            <Fact
              label={t('map.card.range')}
              value={t('map.card.rangeValue', { km: car.range_km })}
            />
            {distance !== null ? (
              <Fact
                label={t('map.card.distance')}
                value={
                  distance.unit === 'meters'
                    ? t('map.card.distanceMeters', { meters: distance.value })
                    : t('map.card.distanceKilometers', { km: distance.value })
                }
              />
            ) : null}
          </View>
          <View style={styles.facts}>
            <Fact
              label={t('map.card.driving')}
              value={t('map.card.perMinute', {
                price: formatKopecks(car.tariff.driving_per_minute, i18n.language),
              })}
            />
            <Fact
              label={t('map.card.parked')}
              value={t('map.card.perMinute', {
                price: formatKopecks(car.tariff.parked_per_minute, i18n.language),
              })}
            />
          </View>
          <ReserveAction reservation={reservation} />
        </>
      )}
      {error !== null ? (
        <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
          {t(`map.errors.${error.kind}`)}
        </AppText>
      ) : null}
      <Button variant="secondary" title={t('map.card.close')} onPress={onClose} />
    </View>
  );
}

function ReserveAction({ reservation }: { reservation: Reservation }) {
  const { t } = useTranslation();
  // A reservation starts the clock, so it is asked for twice rather than on a stray tap.
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<RentalError | null>(null);
  const { kycApproved, reserving } = reservation;

  if (kycApproved === false) {
    return (
      <>
        <AppText tone="muted">{t('map.card.kycRequired')}</AppText>
        <Button title={t('map.card.toDocuments')} onPress={reservation.onOpenDocuments} />
      </>
    );
  }
  return (
    <>
      <RentalErrorMessage error={error} />
      {confirming ? (
        <>
          <AppText>{t('map.card.reserveConfirm')}</AppText>
          <Button
            title={reserving ? t('map.card.reserving') : t('map.card.reserveYes')}
            disabled={reserving}
            onPress={() => {
              setError(null);
              void reservation.onReserve().then(setError);
            }}
          />
          <Button
            variant="secondary"
            title={t('map.card.reserveNo')}
            disabled={reserving}
            onPress={() => {
              setConfirming(false);
            }}
          />
        </>
      ) : (
        <Button
          title={t('map.card.reserve')}
          disabled={kycApproved === null}
          onPress={() => {
            setConfirming(true);
          }}
        />
      )}
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.fact}>
      <AppText variant="caption" tone="muted">
        {label}
      </AppText>
      <AppText variant="label">{value}</AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  // Floats above the map's attribution like the notices, rather than sitting on the edge.
  sheet: {
    borderWidth: 1,
    borderRadius: radius.sheet,
    marginHorizontal: spacing.lg,
    padding: spacing.xl,
    gap: spacing.md,
  },
  photo: { height: photoHeight, width: '100%' },
  facts: { flexDirection: 'row', gap: spacing.xl },
  fact: { gap: spacing.xs },
});
