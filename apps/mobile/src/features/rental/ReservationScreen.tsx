import { radius, spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Image, StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { LazyCarMap } from '../map/LazyCarMap';
import { useClientLocation } from '../map/location';
import type { MapCar } from '../map/mapApi';
import { formatDistance, formatKopecks } from '../map/mapFormat';
import { isExpoGo } from '../map/platform';
import type { Rental } from './rentalApi';
import { ReceiptView } from './ReceiptView';
import { RentalErrorMessage } from './RentalErrorMessage';
import { useRentalStore, useRestoredRental } from './RentalProvider';
import { ReservationCountdown } from './ReservationCountdown';
import type { RentalAction } from './rentalStore';
import { endedText } from './rentalText';
import { distanceMeters, isActive, isOnTrip } from './rentalTime';
import { TripPanel } from './TripPanel';

const photoHeight = 120;
const mapHeight = 220;
// Doors and charge change while the client walks to the car; the deadlines may too.
const refreshEveryMs = 30_000;
// The cost of a trip grows every minute: the server is asked more often then.
const tripRefreshEveryMs = 15_000;

/** The reservation: the car, the countdown, the inspection, then opening the car. */
export function ReservationScreen() {
  const { t, i18n } = useTranslation();
  const { colors } = useTheme();
  const router = useRouter();
  const store = useRentalStore();
  const state = useRestoredRental();
  const { rental } = state;
  const onTrip = rental !== null && isOnTrip(rental);

  useEffect(() => {
    const every = onTrip ? tripRefreshEveryMs : refreshEveryMs;
    const timer = setInterval(() => void store.refresh(), every);
    return () => {
      clearInterval(timer);
    };
  }, [store, onTrip]);

  const toMap = () => {
    store.dismiss();
    router.dismissTo('/');
  };

  if (rental === null) {
    return (
      <Screen>
        {state.restored ? (
          <>
            <RentalErrorMessage error={state.error ?? { kind: 'notFound' }} />
            <Button title={t('rental.toMap')} onPress={toMap} />
          </>
        ) : (
          <>
            <ActivityIndicator color={colors.primary} />
            <AppText tone="muted">{t('rental.loading')}</AppText>
          </>
        )}
      </Screen>
    );
  }
  if (!isActive(rental)) {
    return (
      <Screen>
        <AppText variant="title">{t('rental.title')}</AppText>
        {rental.receipt === null ? (
          <AppText accessibilityRole="alert">{t(endedText(rental))}</AppText>
        ) : (
          <ReceiptView rental={rental} pending={state.pending} />
        )}
        <RentalErrorMessage error={state.error} />
        <Button title={t('rental.toMap')} onPress={toMap} />
      </Screen>
    );
  }
  return (
    <Screen>
      <AppText variant="title">{t('rental.title')}</AppText>
      <CarSummary rental={rental} />
      {rental.status === 'reserved' ? (
        <>
          <StableCountdown rental={rental} offsetMs={state.offsetMs} />
          <AppText variant="caption" tone="muted">
            {t('rental.waitingPrice', {
              price: formatKopecks(rental.tariff.waiting_per_minute, i18n.language),
            })}
          </AppText>
        </>
      ) : (
        <>
          <TripPanel rental={rental} offsetMs={state.offsetMs} pending={state.pending} />
          {state.error?.kind === 'network' ? (
            <AppText tone="muted">{t('rental.trip.offline')}</AppText>
          ) : null}
        </>
      )}
      <RentalErrorMessage error={state.error} />
      {rental.inspection_submitted ? (
        <Commands rental={rental} pending={state.pending} />
      ) : (
        <Card>
          <AppText tone="muted">{t('rental.inspectHint')}</AppText>
          <Button
            title={t('rental.inspect')}
            disabled={state.pending !== null}
            onPress={() => {
              router.push('/rental/inspection');
            }}
          />
        </Card>
      )}
      {rental.status === 'reserved' ? <CancelReservation pending={state.pending} /> : null}
      <Button
        variant="secondary"
        title={t('rental.toMap')}
        onPress={() => {
          router.dismissTo('/');
        }}
      />
    </Screen>
  );
}

function StableCountdown({ rental, offsetMs }: { rental: Rental; offsetMs: number }) {
  const store = useRentalStore();
  // Stable, so the countdown asks the server once when the time is over, not on every tick.
  const refresh = useCallback(() => void store.refresh(), [store]);
  return <ReservationCountdown rental={rental} offsetMs={offsetMs} onOver={refresh} />;
}

function CarSummary({ rental }: { rental: Rental }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const { status, location } = useClientLocation();
  const { car } = rental;
  const carAt = car.lat !== null && car.lon !== null ? { lat: car.lat, lon: car.lon } : null;
  const distance =
    carAt !== null && location !== null
      ? formatDistance(distanceMeters(location, carAt), i18n.language)
      : null;
  const model = `${car.brand} ${car.model}`;

  return (
    <Card>
      {car.photo_url !== null ? (
        <Image
          source={{ uri: car.photo_url }}
          style={styles.photo}
          resizeMode="contain"
          accessibilityLabel={t('map.card.photo', { model })}
        />
      ) : null}
      <AppText variant="label">{model}</AppText>
      <AppText>{t('rental.plate', { plate: car.plate })}</AppText>
      <AppText tone="muted">
        {distance === null
          ? t('rental.distanceUnknown')
          : t('rental.distance', {
              distance:
                distance.unit === 'meters'
                  ? t('rental.meters', { meters: distance.value })
                  : t('rental.kilometers', { km: distance.value }),
            })}
      </AppText>
      {carAt !== null && !isExpoGo ? (
        <View style={[styles.map, { borderColor: theme.colors.border }]}>
          <Suspense fallback={null}>
            <LazyCarMap
              cars={[mapCarOf(rental, carAt)]}
              zones={[]}
              selectedId={car.id}
              focus={carAt}
              showUserLocation={status === 'granted'}
              palette={theme.colors}
              label={t('rental.mapLabel')}
              onAreaChange={() => undefined}
              onCarPress={() => undefined}
              onMapPress={() => undefined}
            />
          </Suspense>
        </View>
      ) : null}
    </Card>
  );
}

export function mapCarOf(rental: Rental, at: { lat: number; lon: number }): MapCar {
  const { car } = rental;
  return {
    id: car.id,
    brand: car.brand,
    model: car.model,
    photo_url: car.photo_url,
    lat: at.lat,
    lon: at.lon,
    battery_pct: car.battery_pct ?? 0,
    range_km: car.range_km ?? 0,
    distance_m: null,
    tariff: {
      driving_per_minute: rental.tariff.driving_per_minute,
      parked_per_minute: rental.tariff.parked_per_minute,
    },
  };
}

function Commands({ rental, pending }: { rental: Rental; pending: RentalAction | null }) {
  const { t } = useTranslation();
  const store = useRentalStore();
  const busy = pending !== null;
  const doors = rental.car.doors_locked;
  return (
    <Card>
      {doors !== null ? (
        <AppText variant="label">{doors ? t('rental.doorsLocked') : t('rental.doorsOpen')}</AppText>
      ) : null}
      <Button
        title={pending === 'open' ? t('rental.opening') : t('rental.open')}
        disabled={busy}
        onPress={() => void store.command('open')}
      />
      <Button
        variant="secondary"
        title={pending === 'close' ? t('rental.closing') : t('rental.close')}
        disabled={busy}
        onPress={() => void store.command('close')}
      />
    </Card>
  );
}

function CancelReservation({ pending }: { pending: RentalAction | null }) {
  const { t } = useTranslation();
  const store = useRentalStore();
  // Cancelling frees the car for everyone: asked for twice rather than on a stray tap.
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <Button
        variant="secondary"
        title={t('rental.cancel')}
        disabled={pending !== null}
        onPress={() => {
          setConfirming(true);
        }}
      />
    );
  }
  return (
    <Card>
      <AppText>{t('rental.cancelConfirm')}</AppText>
      <Button
        title={pending === 'cancel' ? t('rental.cancelling') : t('rental.cancelYes')}
        disabled={pending !== null}
        onPress={() => void store.cancel()}
      />
      <Button
        variant="secondary"
        title={t('rental.cancelNo')}
        disabled={pending !== null}
        onPress={() => {
          setConfirming(false);
        }}
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  photo: { height: photoHeight, width: '100%' },
  map: {
    height: mapHeight,
    borderWidth: 1,
    borderRadius: radius.card,
    overflow: 'hidden',
    marginTop: spacing.sm,
  },
});
