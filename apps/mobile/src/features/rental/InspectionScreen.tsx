import { radius, spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Image, Platform, StyleSheet } from 'react-native';

import { useMounted } from '../../hooks/useMounted';
import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { ProgressBar } from '../../ui/ProgressBar';
import { Screen } from '../../ui/Screen';
import { SegmentedControl } from '../../ui/SegmentedControl';
import { TextField } from '../../ui/TextField';
import { discardPhoto, pickPhoto, type PhotoSource } from '../kyc/photos';
import type { DamageSide, Inspection, PhotoUpload } from './rentalApi';
import { isRetryable, type RentalError } from './rentalErrors';
import { RentalErrorMessage } from './RentalErrorMessage';
import { useRentalState, useRentalStore } from './RentalProvider';

// The order the client walks around the car; after them, any angle that shows damage.
const angles = ['front', 'rear', 'left', 'right', 'interior'] as const;
const sides: readonly DamageSide[] = ['front', 'rear', 'left', 'right', 'roof', 'interior'];
// The server's limit (DAMAGE_COMMENT_MAX_LENGTH): the field stops there instead of a refusal.
const commentMaxLength = 300;
const previewHeight = 180;

/** The inspection before the trip: photos around the car and the damage the client sees. */
export function InspectionScreen() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const router = useRouter();
  const store = useRentalStore();
  const { rental, inspection, error } = useRentalState();

  useEffect(() => {
    void store.loadInspection();
  }, [store]);

  // Submitted, cancelled or expired meanwhile: the reservation screen shows what is next.
  const done = rental === null || rental.status !== 'reserved' || inspection?.submitted_at != null;
  useEffect(() => {
    if (done) router.dismissTo('/rental');
  }, [done, router]);

  if (inspection === null) {
    return (
      <Screen>
        {error === null ? (
          <ActivityIndicator color={colors.primary} />
        ) : (
          <>
            <RentalErrorMessage error={error} />
            <Button title={t('rental.retry')} onPress={() => void store.loadInspection()} />
          </>
        )}
      </Screen>
    );
  }
  return (
    <Screen>
      <AppText variant="title">{t('rental.inspection.title')}</AppText>
      <AppText>
        {t('rental.inspection.subtitle', {
          min: inspection.min_photos,
          max: inspection.max_photos,
        })}
      </AppText>
      <PhotoStep inspection={inspection} />
      <Damages inspection={inspection} />
      <Submit inspection={inspection} />
      <Button
        variant="secondary"
        title={t('rental.inspection.back')}
        onPress={() => {
          router.dismissTo('/rental');
        }}
      />
    </Screen>
  );
}

function PhotoStep({ inspection }: { inspection: Inspection }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const store = useRentalStore();
  const [photo, setPhoto] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<RentalError | null>(null);
  const [picking, setPicking] = useState(false);
  // Refs, not state: the unmount cleanup must see the latest photo and upload.
  const photoRef = useRef<string | null>(null);
  const uploadRef = useRef<PhotoUpload | null>(null);
  const mounted = useMounted();

  useEffect(() => {
    return () => {
      uploadRef.current?.abort();
      if (photoRef.current !== null) discardPhoto(photoRef.current);
    };
  }, []);

  // Every photo leaves the device cache as soon as it is sent or retaken.
  function keepPhoto(uri: string | null) {
    if (photoRef.current !== null && photoRef.current !== uri) discardPhoto(photoRef.current);
    photoRef.current = uri;
    setPhoto(uri);
  }

  async function send(uri: string) {
    setError(null);
    setProgress(0);
    const upload = store.uploadPhoto(uri, setProgress);
    uploadRef.current = upload;
    const result = await upload.done;
    uploadRef.current = null;
    if (!mounted.current) return;
    setProgress(null);
    if (result.ok) keepPhoto(null);
    else if (result.error !== null) setError(result.error);
  }

  // The photo goes up as soon as it is taken: one tap per angle while walking around the car.
  async function take(source: PhotoSource) {
    setPicking(true);
    setError(null);
    try {
      const result = await pickPhoto(source);
      if (result.kind === 'picked') {
        if (!mounted.current) {
          discardPhoto(result.uri);
          return;
        }
        keepPhoto(result.uri);
        void send(result.uri);
      } else if (result.kind === 'cameraDenied') {
        setError({ kind: 'cameraDenied' });
      }
    } catch {
      setError({ kind: 'unknown' });
    } finally {
      if (mounted.current) setPicking(false);
    }
  }

  const count = inspection.photos.length;
  const full = count >= inspection.max_photos;
  const angle = angles[count] ?? 'extra';
  const sending = progress !== null;

  return (
    <Card>
      <AppText variant="label">
        {t('rental.inspection.progress', { count, max: inspection.max_photos })}
      </AppText>
      {!full ? (
        <AppText>
          {t('rental.inspection.next', { angle: t(`rental.inspection.angles.${angle}`) })}
        </AppText>
      ) : null}
      {photo !== null ? (
        <Image
          source={{ uri: photo }}
          accessibilityLabel={t('rental.inspection.preview')}
          resizeMode="contain"
          style={[styles.preview, { backgroundColor: colors.border }]}
        />
      ) : null}
      {sending ? (
        <>
          <ProgressBar
            value={progress}
            accessibilityLabel={t('rental.inspection.uploadProgress')}
          />
          <AppText variant="caption" tone="muted">
            {t('rental.inspection.uploading', { percent: Math.round(progress * 100) })}
          </AppText>
        </>
      ) : null}
      <RentalErrorMessage error={error} />
      {photo !== null && !sending ? (
        <>
          {error !== null && isRetryable(error) ? (
            <Button title={t('rental.inspection.retryUpload')} onPress={() => void send(photo)} />
          ) : null}
          <Button
            variant="secondary"
            title={t('rental.inspection.retake')}
            onPress={() => {
              setError(null);
              keepPhoto(null);
            }}
          />
        </>
      ) : null}
      {photo === null && !full ? (
        <>
          {Platform.OS !== 'web' ? (
            <Button
              title={t('rental.inspection.takePhoto')}
              disabled={picking}
              onPress={() => void take('camera')}
            />
          ) : null}
          <Button
            variant={Platform.OS === 'web' ? 'primary' : 'secondary'}
            title={t('rental.inspection.fromLibrary')}
            disabled={picking}
            onPress={() => void take('library')}
          />
        </>
      ) : null}
    </Card>
  );
}

function Damages({ inspection }: { inspection: Inspection }) {
  const { t } = useTranslation();
  const store = useRentalStore();
  const { pending } = useRentalState();
  const [side, setSide] = useState<DamageSide>('front');
  const [comment, setComment] = useState('');
  const [error, setError] = useState<RentalError | null>(null);
  const trimmed = comment.trim();

  async function add() {
    setError(null);
    const failure = await store.addDamage(side, trimmed);
    if (failure === null) setComment('');
    else setError(failure);
  }

  return (
    <Card>
      <AppText variant="label">{t('rental.inspection.damages')}</AppText>
      {inspection.damages.length === 0 ? (
        <AppText tone="muted">{t('rental.inspection.noDamages')}</AppText>
      ) : (
        inspection.damages.map((damage) => (
          <AppText key={damage.id}>
            {`${t(`rental.inspection.sides.${damage.side}`)}: ${damage.comment}`}
          </AppText>
        ))
      )}
      <SegmentedControl
        label={t('rental.inspection.side')}
        options={sides.map((value) => ({ value, label: t(`rental.inspection.sides.${value}`) }))}
        value={side}
        onChange={setSide}
        disabled={pending !== null}
      />
      <TextField
        label={t('rental.inspection.comment')}
        placeholder={t('rental.inspection.commentPlaceholder')}
        value={comment}
        onChangeText={setComment}
        maxLength={commentMaxLength}
        multiline
      />
      <RentalErrorMessage error={error} />
      <Button
        variant="secondary"
        title={t('rental.inspection.addDamage')}
        disabled={pending !== null || trimmed === ''}
        onPress={() => void add()}
      />
    </Card>
  );
}

function Submit({ inspection }: { inspection: Inspection }) {
  const { t } = useTranslation();
  const store = useRentalStore();
  const { pending, uploading } = useRentalState();
  const [error, setError] = useState<RentalError | null>(null);
  const missing = Math.max(0, inspection.min_photos - inspection.photos.length);

  return (
    <>
      {missing > 0 ? (
        <AppText tone="muted">{t('rental.inspection.needMore', { count: missing })}</AppText>
      ) : null}
      <RentalErrorMessage error={error} />
      <Button
        title={t('rental.inspection.submit')}
        // A photo still on its way would be refused once the inspection is closed, and lost.
        disabled={missing > 0 || pending !== null || uploading > 0}
        onPress={() => {
          setError(null);
          void store.submitInspection().then(setError);
        }}
      />
    </>
  );
}

const styles = StyleSheet.create({
  preview: {
    height: previewHeight,
    width: '100%',
    borderRadius: radius.card,
    marginVertical: spacing.sm,
  },
});
