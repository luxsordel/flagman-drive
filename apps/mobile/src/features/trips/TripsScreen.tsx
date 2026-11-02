import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Pressable } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { formatKopecks } from '../map/mapFormat';
import type { HistoryItem, HistoryPage, RentalResult } from '../rental/rentalApi';
import { RentalErrorMessage } from '../rental/RentalErrorMessage';
import type { RentalError } from '../rental/rentalErrors';
import { useRentalStore } from '../rental/RentalProvider';

const active = new Set(['reserved', 'in_trip', 'parked']);

/** The client's trips, newest first; an ended one opens its receipt, with the debt to pay. */
export function TripsScreen() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const store = useRentalStore();
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<RentalError | null>(null);

  // Counts the requests: only the answer to the latest one is shown, a reload on return wins over
  // a next page asked for before it.
  const latest = useRef(0);

  const accept = useCallback((result: RentalResult<HistoryPage>, more: boolean, asked: number) => {
    if (asked !== latest.current) return;
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setItems((known) => (more && known ? [...known, ...result.value.items] : result.value.items));
    setCursor(result.value.next_cursor);
  }, []);

  // Again on every return: a debt paid on the receipt screen must not stay marked here.
  useFocusEffect(
    useCallback(() => {
      latest.current += 1;
      const asked = latest.current;
      void store.history().then((result) => {
        accept(result, false, asked);
      });
    }, [store, accept]),
  );

  const loadMore = async () => {
    setLoading(true);
    const more = cursor !== null && items !== null;
    latest.current += 1;
    const asked = latest.current;
    accept(await store.history(more ? cursor : undefined), more, asked);
    setLoading(false);
  };

  return (
    <Screen>
      <AppText variant="title">{t('trips.title')}</AppText>
      {items === null && error === null && <ActivityIndicator color={colors.primary} />}
      {items?.length === 0 && <AppText tone="muted">{t('trips.empty')}</AppText>}
      {items?.map((item) => (
        <TripRow key={item.id} item={item} />
      ))}
      <RentalErrorMessage error={error} />
      {(cursor !== null || (error !== null && items === null)) && (
        <Button
          variant="secondary"
          title={loading ? t('trips.loading') : items === null ? t('trips.retry') : t('trips.more')}
          disabled={loading}
          onPress={() => void loadMore()}
        />
      )}
    </Screen>
  );
}

function TripRow({ item }: { item: HistoryItem }) {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const ongoing = active.has(item.status);
  const when = new Date(item.reserved_at).toLocaleString(i18n.language);
  const minutes =
    item.trip_started_at && item.ended_at
      ? Math.max(
          1,
          Math.round(
            (new Date(item.ended_at).getTime() - new Date(item.trip_started_at).getTime()) / 60_000,
          ),
        )
      : null;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={() => {
        if (ongoing) router.push('/rental');
        else router.push({ pathname: '/rental/receipt', params: { id: item.id } });
      }}
    >
      <Card>
        <AppText variant="label">
          {item.car.brand} {item.car.model} · {item.car.plate}
        </AppText>
        <AppText tone="muted">{when}</AppText>
        {minutes !== null && <AppText>{t('trips.duration', { minutes })}</AppText>}
        {ongoing ? (
          <AppText>{t('trips.ongoing')}</AppText>
        ) : (
          <AppText>{formatKopecks(item.total, i18n.language)}</AppText>
        )}
        {item.status === 'cancelled' && <AppText tone="muted">{t('trips.cancelled')}</AppText>}
        {item.charge_status === 'paid' && <AppText tone="muted">{t('trips.paid')}</AppText>}
        {item.charge_status === 'failed' && <AppText tone="danger">{t('trips.debt')}</AppText>}
        {item.charge_status === 'pending' && <AppText tone="muted">{t('trips.paying')}</AppText>}
      </Card>
    </Pressable>
  );
}
