import { useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import type { Fine } from '../rental/rentalApi';
import { RentalErrorMessage } from '../rental/RentalErrorMessage';
import type { RentalError } from '../rental/rentalErrors';
import { useRentalStore } from '../rental/RentalProvider';
import { FineList } from './FineList';

/** Loads the client's fines, of one trip with `rentalId`, again whenever the screen is shown. */
export function useFines(rentalId?: string) {
  const store = useRentalStore();
  const [fines, setFines] = useState<Fine[] | null>(null);
  const [error, setError] = useState<RentalError | null>(null);
  // Only the answer to the latest request is shown.
  const latest = useRef(0);

  const load = useCallback(() => {
    latest.current += 1;
    const asked = latest.current;
    void store.fines(rentalId).then((result) => {
      if (asked !== latest.current) return;
      if (result.ok) {
        setFines(result.value);
        setError(null);
      } else {
        setError(result.error);
      }
    });
  }, [store, rentalId]);

  // Again on every return: a fine cancelled in the chat meanwhile must not stay unpaid here.
  useFocusEffect(load);

  const replace = useCallback((changed: Fine) => {
    setFines((known) => known?.map((fine) => (fine.id === changed.id ? changed : fine)) ?? null);
  }, []);

  return { fines, error, load, replace };
}

/** «My fines»: every fine of the client, newest first. */
export function FinesScreen() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const { fines, error, load, replace } = useFines();

  return (
    <Screen>
      <AppText variant="title">{t('fines.title')}</AppText>
      {fines === null && error === null && <ActivityIndicator color={colors.primary} />}
      {fines?.length === 0 && <AppText tone="muted">{t('fines.empty')}</AppText>}
      {fines !== null && <FineList fines={fines} onChanged={replace} />}
      <RentalErrorMessage error={error} />
      {fines === null && error !== null && (
        <Button variant="secondary" title={t('fines.retry')} onPress={load} />
      )}
    </Screen>
  );
}

/** The fines of one trip on its receipt; nothing when it has none. */
export function TripFines({ rentalId }: { rentalId: string }) {
  const { t } = useTranslation();
  const { fines, replace } = useFines(rentalId);
  if (fines === null || fines.length === 0) return null;
  return (
    <>
      <AppText variant="label">{t('fines.ofTrip')}</AppText>
      <FineList fines={fines} onChanged={replace} />
    </>
  );
}
