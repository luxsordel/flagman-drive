import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Image, StyleSheet } from 'react-native';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { formatKopecks } from '../map/mapFormat';
import type { Fine } from '../rental/rentalApi';
import { RentalErrorMessage } from '../rental/RentalErrorMessage';
import type { RentalError } from '../rental/rentalErrors';
import { useRentalStore } from '../rental/RentalProvider';

/** The name of the fine in the app's language; Russian for anything but English. */
function fineName(fine: Fine, language: string): string {
  return language.startsWith('en') ? fine.name_en : fine.name_ru;
}

/**
 * Fines with their reason, amount, evidence and payment. An unpaid one is paid with a button,
 * like a debt; a disputed one goes to the support chat with its trip attached.
 */
export function FineList({ fines, onChanged }: { fines: Fine[]; onChanged: (fine: Fine) => void }) {
  return (
    <>
      {fines.map((fine) => (
        <FineCard key={fine.id} fine={fine} onChanged={onChanged} />
      ))}
    </>
  );
}

function FineCard({ fine, onChanged }: { fine: Fine; onChanged: (fine: Fine) => void }) {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const store = useRentalStore();
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState<RentalError | null>(null);

  const pay = async () => {
    setPaying(true);
    setError(null);
    const result = await store.payFine(fine.id);
    setPaying(false);
    if (result.ok) onChanged(result.value);
    else setError(result.error);
  };

  const unpaid = fine.status === 'unpaid';
  return (
    <Card>
      <AppText variant="label">{fineName(fine, i18n.language)}</AppText>
      <AppText tone={unpaid ? 'danger' : 'default'}>
        {formatKopecks(fine.amount, i18n.language)} · {t(`fines.statuses.${fine.status}`)}
      </AppText>
      <AppText tone="muted">{new Date(fine.created_at).toLocaleString(i18n.language)}</AppText>
      {fine.comment !== null && <AppText>{fine.comment}</AppText>}
      {fine.automatic && <AppText tone="muted">{t('fines.automatic')}</AppText>}
      {fine.cancel_reason !== null && (
        <AppText tone="muted">{t('fines.cancelled', { reason: fine.cancel_reason })}</AppText>
      )}
      {fine.refund_status !== null && (
        <AppText tone="muted">{t(`fines.refunds.${fine.refund_status}`)}</AppText>
      )}
      {fine.photos.map((photo) => (
        <FinePhoto key={photo.id} fineId={fine.id} photoId={photo.id} />
      ))}
      <RentalErrorMessage error={error} />
      {unpaid && (
        <Button
          title={paying ? t('fines.paying') : t('fines.pay')}
          disabled={paying}
          onPress={() => void pay()}
        />
      )}
      {fine.status !== 'cancelled' && (
        <Button
          variant="secondary"
          title={t('fines.dispute')}
          onPress={() => {
            router.push({ pathname: '/support', params: { rentalId: fine.rental_id } });
          }}
        />
      )}
    </Card>
  );
}

/** The link is asked for on a tap, as in the support chat: it lives for minutes. */
function FinePhoto({ fineId, photoId }: { fineId: string; photoId: string }) {
  const { t } = useTranslation();
  const store = useRentalStore();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  if (url !== null) {
    return (
      <Image
        source={{ uri: url }}
        style={styles.photo}
        resizeMode="contain"
        accessibilityLabel={t('fines.photo')}
      />
    );
  }
  return (
    <>
      <Button
        variant="secondary"
        title={t('fines.showPhoto')}
        onPress={() => {
          setFailed(false);
          void store.finePhotoUrl(fineId, photoId).then((link) => {
            if (link.ok) setUrl(link.value);
            else setFailed(true);
          });
        }}
      />
      {failed && <AppText tone="danger">{t('fines.photoFailed')}</AppText>}
    </>
  );
}

const styles = StyleSheet.create({
  // The size of the support chat's photos: the same evidence, the same column.
  photo: { width: 240, height: 240 },
});
