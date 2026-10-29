import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import type { Rental } from './rentalApi';
import { formatCountdown, reservationPhase } from './rentalTime';

/** Now on the server's clock, ticking once a second while `running`. */
export function useServerNow(offsetMs: number, running: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [running]);
  return now + offsetMs;
}

/**
 * The free wait, then the paid one until the reservation is cancelled. When the time is over the
 * server cancels the reservation: `onOver` asks it for the result. Pass a stable function: it is
 * called again whenever it changes while the time is over.
 */
export function ReservationCountdown({
  rental,
  offsetMs,
  onOver,
}: {
  rental: Rental;
  offsetMs: number;
  onOver: () => void;
}) {
  const { t } = useTranslation();
  const serverNow = useServerNow(offsetMs, true);
  const phase = reservationPhase(rental, serverNow);
  const over = phase.kind === 'over';

  useEffect(() => {
    if (over) onOver();
  }, [over, onOver]);

  const text =
    phase.kind === 'free'
      ? t('rental.free', { time: formatCountdown(phase.leftMs) })
      : phase.kind === 'paid'
        ? t('rental.paid', { time: formatCountdown(phase.leftMs) })
        : t('rental.over');
  return (
    <AppText
      variant="label"
      tone={phase.kind === 'free' ? 'default' : 'danger'}
      // Read out on a change of phase, not every second.
      accessibilityLiveRegion="none"
    >
      {text}
    </AppText>
  );
}
