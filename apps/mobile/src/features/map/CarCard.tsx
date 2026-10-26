import { radius, spacing } from '@flagman/design-tokens';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Image, StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { formatDistance } from './mapFormat';
import type { SelectedCar } from './mapStore';

const photoHeight = 120;

/** The bottom card of the car picked on the map. Booking comes with rentals (stage 3). */
export function CarCard({ selected, onClose }: { selected: SelectedCar; onClose: () => void }) {
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
          <AppText tone="muted">{t('map.card.bookingSoon')}</AppText>
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
