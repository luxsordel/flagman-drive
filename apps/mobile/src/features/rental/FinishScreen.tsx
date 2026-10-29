import { radius, spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { Suspense, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { LazyCarMap } from '../map/LazyCarMap';
import { useMapState } from '../map/MapProvider';
import { isExpoGo } from '../map/platform';
import type { Rental } from './rentalApi';
import type { RentalError, RentalErrorKind } from './rentalErrors';
import { RentalErrorMessage } from './RentalErrorMessage';
import { useRentalState, useRentalStore } from './RentalProvider';
import { mapCarOf } from './ReservationScreen';
import { isOnTrip } from './rentalTime';

const mapHeight = 260;

/** In the order the server checks them: it tells the first one that fails, never the rest. */
const checks = ['inspection', 'telemetry', 'zone', 'ignition', 'doors'] as const;
type Check = (typeof checks)[number];
type CheckState = 'passed' | 'failed' | 'unchecked';

const checkOfError: Partial<Record<RentalErrorKind, Check>> = {
  inspectionRequired: 'inspection',
  carStateStale: 'telemetry',
  carLocationUnknown: 'telemetry',
  outsideZone: 'zone',
  ignitionOn: 'ignition',
  doorsOpen: 'doors',
  lockFailed: 'doors',
};

/** Where each check stands after the last attempt; the ones the server did not reach are open. */
export function checkStates(rental: Rental, error: RentalError | null): Record<Check, CheckState> {
  const failed = error === null ? undefined : checkOfError[error.kind];
  const failedAt = failed === undefined ? -1 : checks.indexOf(failed);
  const states = {} as Record<Check, CheckState>;
  checks.forEach((check, index) => {
    if (failedAt >= 0) {
      states[check] = index < failedAt ? 'passed' : index === failedAt ? 'failed' : 'unchecked';
    } else if (check === 'inspection') {
      states[check] = rental.after_inspection_submitted ? 'passed' : 'failed';
    } else {
      states[check] = 'unchecked';
    }
  });
  return states;
}

/** The end of the trip: the inspection after it, the server's checks, then the receipt. */
export function FinishScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const store = useRentalStore();
  const { rental, error, pending } = useRentalState();

  // Finished (the receipt is on the rental screen) or no longer on a trip.
  const done = rental === null || !isOnTrip(rental);
  useEffect(() => {
    if (done) router.dismissTo('/rental');
  }, [done, router]);
  if (rental === null || done) return null;

  const states = checkStates(rental, error);
  const finishing = pending === 'finish';
  return (
    <Screen>
      <AppText variant="title">{t('rental.finish.title')}</AppText>
      <AppText tone="muted">{t('rental.finish.subtitle')}</AppText>
      <Card>
        {checks.map((check) => (
          <AppText
            key={check}
            tone={states[check] === 'failed' ? 'danger' : 'default'}
            accessibilityLabel={`${t(`rental.finish.checks.${check}`)}: ${t(
              `rental.finish.${states[check]}`,
            )}`}
          >
            {mark[states[check]]} {t(`rental.finish.checks.${check}`)} —{' '}
            {t(`rental.finish.${states[check]}`)}
          </AppText>
        ))}
      </Card>
      <RentalErrorMessage error={error} />
      {error?.kind === 'outsideZone' ? <ZoneMap rental={rental} /> : null}
      {rental.after_inspection_submitted ? null : (
        <Button
          variant="secondary"
          title={t('rental.finish.inspect')}
          disabled={pending !== null}
          onPress={() => {
            router.push('/rental/inspection');
          }}
        />
      )}
      <Button
        title={finishing ? t('rental.finish.finishing') : t('rental.finish.finish')}
        disabled={pending !== null || !rental.after_inspection_submitted}
        onPress={() => void store.finish()}
      />
      <Button
        variant="secondary"
        title={t('rental.finish.back')}
        onPress={() => {
          router.dismissTo('/rental');
        }}
      />
    </Screen>
  );
}

const mark: Record<CheckState, string> = { passed: '✓', failed: '✗', unchecked: '•' };

function ZoneMap({ rental }: { rental: Rental }) {
  const { t } = useTranslation();
  const theme = useTheme();
  const { zones } = useMapState();
  const { car } = rental;
  const carAt = car.lat !== null && car.lon !== null ? { lat: car.lat, lon: car.lon } : null;
  return (
    <Card>
      <AppText>{t('rental.finish.zoneHint')}</AppText>
      {carAt !== null && !isExpoGo ? (
        <View style={[styles.map, { borderColor: theme.colors.border }]}>
          <Suspense fallback={null}>
            <LazyCarMap
              cars={[mapCarOf(rental, carAt)]}
              zones={zones}
              selectedId={car.id}
              focus={carAt}
              showUserLocation={false}
              palette={theme.colors}
              label={t('rental.finish.zoneMapLabel')}
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

const styles = StyleSheet.create({
  map: {
    height: mapHeight,
    borderWidth: 1,
    borderRadius: radius.card,
    overflow: 'hidden',
    marginTop: spacing.sm,
  },
});
