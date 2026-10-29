import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { SegmentedControl } from '../../ui/SegmentedControl';
import { formatKopecks } from '../map/mapFormat';
import type { Rental } from './rentalApi';
import { useRentalStore } from './RentalProvider';
import type { RentalAction } from './rentalStore';
import { formatCountdown, tripElapsedMs } from './rentalTime';

type Mode = 'driving' | 'parked';
const tickMs = 1000;

/** The trip: its time, the cost the server counted, the car's charge and the parking switch. */
export function TripPanel({
  rental,
  offsetMs,
  pending,
}: {
  rental: Rental;
  offsetMs: number;
  pending: RentalAction | null;
}) {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const store = useRentalStore();
  const { car, tariff } = rental;
  const mode: Mode = rental.status === 'parked' ? 'parked' : 'driving';
  const busy = pending !== null;

  return (
    <Card>
      <AppText variant="label">{t('rental.trip.title')}</AppText>
      <TripTimer rental={rental} offsetMs={offsetMs} />
      <AppText variant="title">
        {t('rental.trip.cost', { amount: formatKopecks(rental.cost.total, i18n.language) })}
      </AppText>
      <AppText variant="caption" tone="muted">
        {t('rental.trip.costHint')}
      </AppText>
      {car.battery_pct === null ? (
        <AppText tone="muted">{t('rental.trip.noTelemetry')}</AppText>
      ) : (
        <>
          <AppText>{t('rental.trip.battery', { pct: car.battery_pct })}</AppText>
          {car.range_km !== null ? (
            <AppText>{t('rental.trip.range', { km: car.range_km })}</AppText>
          ) : null}
        </>
      )}
      {rental.warnings.map((warning) => (
        <AppText key={warning} tone="danger" accessibilityRole="alert">
          {t(`rental.trip.warnings.${warning}`)}
        </AppText>
      ))}
      <SegmentedControl<Mode>
        label={t('rental.trip.mode')}
        options={[
          { value: 'driving', label: t('rental.trip.driving') },
          { value: 'parked', label: t('rental.trip.parked') },
        ]}
        value={mode}
        disabled={busy}
        onChange={(next) => void (next === 'parked' ? store.park() : store.resume())}
      />
      <AppText variant="caption" tone="muted">
        {mode === 'parked'
          ? t('rental.trip.parkedPrice', {
              price: formatKopecks(tariff.parked_per_minute, i18n.language),
            })
          : t('rental.trip.drivingPrice', {
              price: formatKopecks(tariff.driving_per_minute, i18n.language),
            })}
      </AppText>
      <Button
        title={t('rental.trip.finish')}
        disabled={busy}
        onPress={() => {
          router.push('/rental/finish');
        }}
      />
    </Card>
  );
}

/** Ticks by itself on the server's clock, so the time runs on without the network too. */
function TripTimer({ rental, offsetMs }: { rental: Rental; offsetMs: number }) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, tickMs);
    return () => {
      clearInterval(timer);
    };
  }, []);
  return (
    <AppText accessibilityRole="timer">
      {t('rental.trip.elapsed', { time: formatCountdown(tripElapsedMs(rental, now + offsetMs)) })}
    </AppText>
  );
}
