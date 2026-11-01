import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { SegmentedControl } from '../../ui/SegmentedControl';
import { TextField } from '../../ui/TextField';
import { discardPhoto, pickPhoto, type PhotoSource } from '../kyc/photos';
import { locateOnce } from '../map/location';
import { useSupport } from './SupportProvider';
import type { IncidentType, SupportError } from './supportApi';

/** MAX_INCIDENT_PHOTOS of the API (support/domain.py). */
const maxPhotos = 8;
const types: readonly IncidentType[] = ['accident', 'damage', 'malfunction', 'other'];

/** Reports an accident, damage or a fault; photos follow the report one by one. */
export function IncidentScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { api } = useSupport();
  const { rentalId } = useLocalSearchParams<{ rentalId?: string }>();
  const [type, setType] = useState<IncidentType>('damage');
  const [description, setDescription] = useState('');
  const [photos, setPhotos] = useState<string[]>([]);
  const [incidentId, setIncidentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<SupportError | null>(null);
  const [cameraDenied, setCameraDenied] = useState(false);
  const [done, setDone] = useState(false);
  // A ref, not state: the unmount cleanup must see the latest list.
  const left = useRef<string[]>([]);

  useEffect(() => {
    return () => {
      left.current.forEach(discardPhoto);
    };
  }, []);

  const keep = (next: string[]) => {
    left.current = next;
    setPhotos(next);
  };

  const attach = async (source: PhotoSource) => {
    setCameraDenied(false);
    const picked = await pickPhoto(source);
    if (picked.kind === 'cameraDenied') setCameraDenied(true);
    if (picked.kind === 'picked') keep([...left.current, picked.uri]);
  };

  /** Once the report is saved a retry sends only the photos left, not a second report. */
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      let id = incidentId;
      if (id === null) {
        const fix = await locateOnce();
        const report = await api.reportIncident({
          type,
          description: description.trim(),
          location: fix.kind === 'located' ? fix.location : null,
          rentalId: rentalId ?? null,
        });
        if (!report.ok) {
          setError(report.error);
          return;
        }
        id = report.value.id;
        setIncidentId(id);
      }
      for (const photo of left.current) {
        const sent = await api.addIncidentPhoto(id, photo);
        if (!sent.ok) {
          setError(sent.error);
          return;
        }
        discardPhoto(photo);
        keep(left.current.filter((uri) => uri !== photo));
      }
      setDone(true);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen>
        <AppText variant="title">{t('support.incident.sentTitle')}</AppText>
        <AppText>{t('support.incident.sentText')}</AppText>
        <Button
          title={t('support.incident.toChat')}
          onPress={() => {
            router.replace('/support');
          }}
        />
        <Button
          variant="secondary"
          title={t('support.back')}
          onPress={() => {
            router.back();
          }}
        />
      </Screen>
    );
  }

  const reported = incidentId !== null;
  return (
    <Screen>
      <AppText variant="title">{t('support.incident.title')}</AppText>
      <SegmentedControl
        label={t('support.incident.type')}
        options={types.map((value) => ({ value, label: t(`support.incident.types.${value}`) }))}
        value={type}
        onChange={setType}
        disabled={busy || reported}
      />
      <TextField
        label={t('support.incident.description')}
        value={description}
        onChangeText={setDescription}
        multiline
        maxLength={2000}
        editable={!busy && !reported}
      />
      <AppText tone="muted">{t('support.incident.photos', { count: photos.length })}</AppText>
      {photos.length < maxPhotos && (
        <>
          <Button
            variant="secondary"
            title={t('support.camera')}
            disabled={busy}
            onPress={() => void attach('camera')}
          />
          <Button
            variant="secondary"
            title={t('support.library')}
            disabled={busy}
            onPress={() => void attach('library')}
          />
        </>
      )}
      {cameraDenied && (
        <AppText tone="danger" accessibilityRole="alert">
          {t('support.cameraDenied')}
        </AppText>
      )}
      {error !== null && (
        <AppText tone="danger" accessibilityRole="alert">
          {t(`support.errors.${error.kind}`)}
        </AppText>
      )}
      <Button
        title={t(reported ? 'support.incident.sendPhotos' : 'support.incident.submit')}
        disabled={busy || !description.trim()}
        onPress={() => void submit()}
      />
      <Button
        variant="secondary"
        title={t('support.back')}
        onPress={() => {
          router.back();
        }}
      />
    </Screen>
  );
}
