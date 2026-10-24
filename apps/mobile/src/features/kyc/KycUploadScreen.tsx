import { radius, spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Image, Platform, StyleSheet, View } from 'react-native';

import { useMounted } from '../../hooks/useMounted';
import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { ProgressBar } from '../../ui/ProgressBar';
import { Screen } from '../../ui/Screen';
import type { DocumentKind, KycApplication, UploadHandle } from './kycApi';
import { isRetryable, type KycError } from './kycErrors';
import { KycErrorMessage } from './KycErrorMessage';
import { canEditDocuments, documentSteps, firstStep, isUploaded, stepAfter } from './kycFlow';
import { useKycApplication, useKycStore } from './KycProvider';
import { discardPhoto, pickPhoto, type PhotoSource, usesInAppCamera } from './photos';
import { allowSelfieCamera, SelfieCamera } from './SelfieCamera';

/** Step-by-step document upload, then the overview to send the application for review. */
export function KycUploadScreen() {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const store = useKycStore();
  const { application, loading, loadError } = useKycApplication();

  if (application === null) {
    return (
      <Screen>
        {loadError === null ? (
          <ActivityIndicator color={colors.primary} />
        ) : (
          <>
            <KycErrorMessage error={loadError} />
            <Button
              title={t('kyc.upload.retry')}
              disabled={loading}
              onPress={() => void store.load()}
            />
          </>
        )}
      </Screen>
    );
  }
  // Sent for review (here or from another device): the documents can no longer change.
  if (!canEditDocuments(application.status)) return <BackToStatus />;
  return <UploadFlow application={application} />;
}

// The client usually came here from the status screen: going back to it, rather than redirecting,
// leaves no second status screen in the stack.
function BackToStatus() {
  const router = useRouter();
  useEffect(() => {
    router.dismissTo('/kyc');
  }, [router]);
  return null;
}

function UploadFlow({ application }: { application: KycApplication }) {
  const [step, setStep] = useState<DocumentKind | 'review'>(() => firstStep(application));
  if (step === 'review') return <DocumentsReview application={application} onOpen={setStep} />;
  return (
    <DocumentStep
      key={step}
      kind={step}
      onUploaded={(updated) => {
        setStep(stepAfter(updated, step));
      }}
      onOverview={() => {
        setStep('review');
      }}
    />
  );
}

function DocumentStep({
  kind,
  onUploaded,
  onOverview,
}: {
  kind: DocumentKind;
  onUploaded: (application: KycApplication) => void;
  onOverview: () => void;
}) {
  const { t } = useTranslation();
  const store = useKycStore();
  const [photo, setPhoto] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<KycError | null>(null);
  const [picking, setPicking] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const selfie = kind === 'selfie_with_license';
  // Refs, not state: the unmount cleanup must see the latest photo and upload.
  const photoRef = useRef<string | null>(null);
  const uploadRef = useRef<UploadHandle | null>(null);
  const mounted = useMounted();

  useEffect(() => {
    return () => {
      uploadRef.current?.abort();
      if (photoRef.current !== null) discardPhoto(photoRef.current);
    };
  }, []);

  // Every photo leaves the device cache as soon as it is sent or replaced.
  function keepPhoto(uri: string | null) {
    if (photoRef.current !== null && photoRef.current !== uri) discardPhoto(photoRef.current);
    photoRef.current = uri;
    setPhoto(uri);
  }

  async function pick(source: PhotoSource) {
    setPicking(true);
    setError(null);
    try {
      if (source === 'camera' && usesInAppCamera({ selfie })) {
        if (!(await allowSelfieCamera())) setError({ kind: 'cameraDenied' });
        else if (mounted.current) setCameraOpen(true);
        return;
      }
      const result = await pickPhoto(source, { selfie });
      if (result.kind === 'picked') {
        // The client left the step while the camera was open.
        if (!mounted.current) discardPhoto(result.uri);
        else keepPhoto(result.uri);
      } else if (result.kind === 'cameraDenied') {
        setError({ kind: 'cameraDenied' });
      }
    } catch {
      setError({ kind: 'unknown' });
    } finally {
      if (mounted.current) setPicking(false);
    }
  }

  async function send(uri: string) {
    setError(null);
    setProgress(0);
    const upload = store.upload(kind, uri, setProgress);
    uploadRef.current = upload;
    const result = await upload.done;
    uploadRef.current = null;
    if (!mounted.current) return;
    setProgress(null);
    if (result.ok) {
      keepPhoto(null);
      onUploaded(result.application);
    } else if (result.error !== null) {
      setError(result.error);
    }
  }

  const sending = progress !== null;
  const stepNumber = documentSteps.indexOf(kind) + 1;
  const title = t(`kyc.steps.${kind}.title`);

  if (cameraOpen) {
    return (
      <SelfieCamera
        onTaken={(uri) => {
          setCameraOpen(false);
          keepPhoto(uri);
        }}
        onCancel={() => {
          setCameraOpen(false);
        }}
        onError={() => {
          setCameraOpen(false);
          setError({ kind: 'unknown' });
        }}
      />
    );
  }

  return (
    <Screen>
      <AppText variant="caption" tone="muted">
        {t('kyc.upload.step', { current: stepNumber, total: documentSteps.length })}
      </AppText>
      <AppText variant="title">{title}</AppText>
      <AppText>{t(`kyc.steps.${kind}.hint`)}</AppText>
      {photo !== null && (
        <PhotoPreview uri={photo} label={t('kyc.upload.preview', { document: title })} />
      )}
      {sending && (
        <>
          <ProgressBar value={progress} accessibilityLabel={title} />
          <AppText variant="caption" tone="muted">
            {t('kyc.upload.sending', { percent: Math.round(progress * 100) })}
          </AppText>
        </>
      )}
      <KycErrorMessage error={error} />
      {photo === null ? (
        <>
          {Platform.OS !== 'web' && (
            <Button
              title={t('kyc.upload.takePhoto')}
              disabled={picking}
              onPress={() => void pick('camera')}
            />
          )}
          <Button
            variant={Platform.OS === 'web' ? 'primary' : 'secondary'}
            title={t(Platform.OS === 'web' ? 'kyc.upload.chooseFile' : 'kyc.upload.fromGallery')}
            disabled={picking}
            onPress={() => void pick('library')}
          />
        </>
      ) : (
        !sending && (
          <>
            {(error === null || isRetryable(error)) && (
              <Button
                title={t(error === null ? 'kyc.upload.send' : 'kyc.upload.retry')}
                onPress={() => void send(photo)}
              />
            )}
            <Button
              variant="secondary"
              title={t('kyc.upload.retake')}
              onPress={() => {
                setError(null);
                keepPhoto(null);
              }}
            />
          </>
        )
      )}
      <Button
        variant="secondary"
        title={t('kyc.upload.toOverview')}
        disabled={sending}
        onPress={onOverview}
      />
    </Screen>
  );
}

function PhotoPreview({ uri, label }: { uri: string; label: string }) {
  const { colors } = useTheme();
  return (
    <Image
      source={{ uri }}
      accessibilityLabel={label}
      resizeMode="contain"
      style={[styles.preview, { backgroundColor: colors.border }]}
    />
  );
}

function DocumentsReview({
  application,
  onOpen,
}: {
  application: KycApplication;
  onOpen: (kind: DocumentKind) => void;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const store = useKycStore();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<KycError | null>(null);

  // On success the application is under review and the screen redirects to the status by itself.
  async function submit() {
    setBusy(true);
    setError(null);
    const failure = await store.submit();
    setBusy(false);
    setError(failure);
  }

  return (
    <Screen>
      <AppText variant="title">{t('kyc.review.title')}</AppText>
      <AppText>{t('kyc.review.subtitle')}</AppText>
      {documentSteps.map((kind) => {
        const uploaded = isUploaded(application, kind);
        return (
          <Card key={kind}>
            <View style={styles.row}>
              <View style={styles.rowText}>
                <AppText variant="label">{t(`kyc.steps.${kind}.title`)}</AppText>
                <AppText variant="caption" tone={uploaded ? 'muted' : 'danger'}>
                  {t(uploaded ? 'kyc.review.uploaded' : 'kyc.review.missing')}
                </AppText>
              </View>
              <Button
                variant="secondary"
                title={t(uploaded ? 'kyc.review.replace' : 'kyc.review.upload')}
                disabled={busy}
                onPress={() => {
                  onOpen(kind);
                }}
              />
            </View>
          </Card>
        );
      })}
      <KycErrorMessage error={error} />
      <Button
        title={t('kyc.review.submit')}
        disabled={busy || application.missing_documents.length > 0}
        onPress={() => void submit()}
      />
      <Button
        variant="secondary"
        title={t('kyc.review.back')}
        disabled={busy}
        onPress={() => {
          router.back();
        }}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  preview: { width: '100%', aspectRatio: 4 / 3, borderRadius: radius.card },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  rowText: { flex: 1, gap: spacing.xs },
});
