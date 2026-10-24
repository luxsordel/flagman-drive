import { spacing } from '@flagman/design-tokens';
import { Camera, CameraView } from 'expo-camera';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BackHandler, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useMounted } from '../../hooks/useMounted';
import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { discardPhoto, replaceWithJpeg } from './photos';

export interface SelfieCameraProps {
  /** A JPEG copy in the app cache, as from `pickPhoto`: the caller discards it once sent. */
  onTaken: (uri: string) => void;
  onCancel: () => void;
  /** The camera did not start or the shot failed. */
  onError: () => void;
}

/** Asks for the camera permission; the caller does it before showing `SelfieCamera`. */
export async function allowSelfieCamera(): Promise<boolean> {
  const permission = await Camera.requestCameraPermissionsAsync();
  return permission.granted;
}

/** The app's own front camera for the selfie with the licence. */
export function SelfieCamera({ onTaken, onCancel, onError }: SelfieCameraProps) {
  const { colors } = useTheme();
  const { t } = useTranslation();
  const camera = useRef<CameraView>(null);
  const [ready, setReady] = useState(false);
  const [taking, setTaking] = useState(false);
  // `disabled` only applies after a re-render: a quick double tap would start a second shot,
  // and its failure would hide the first one that worked.
  const takingRef = useRef(false);
  const mounted = useMounted();

  // The system back button leaves the camera, not the whole step, as it does for the system camera.
  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      onCancel();
      return true;
    });
    return () => {
      subscription.remove();
    };
  }, [onCancel]);

  async function take() {
    const view = camera.current;
    if (view === null || takingRef.current) return;
    takingRef.current = true;
    setTaking(true);
    try {
      // `exif: false` only keeps the metadata out of JS; the JPEG re-encode drops it from the file.
      const picture = await view.takePictureAsync({ exif: false, quality: 1 });
      const uri = await replaceWithJpeg(picture);
      if (mounted.current) onTaken(uri);
      else discardPhoto(uri);
    } catch {
      takingRef.current = false;
      if (mounted.current) {
        setTaking(false);
        onError();
      }
    }
  }

  return (
    <SafeAreaView style={[styles.fill, { backgroundColor: colors.background }]}>
      <View style={styles.content}>
        <AppText>{t('kyc.steps.selfie_with_license.hint')}</AppText>
        <CameraView
          ref={camera}
          style={styles.fill}
          facing="front"
          // A mirrored shot would show the licence text backwards.
          mirror={false}
          animateShutter
          accessibilityLabel={t('kyc.camera.preview')}
          onCameraReady={() => {
            setReady(true);
          }}
          onMountError={onError}
        />
        <Button
          title={t('kyc.camera.shoot')}
          disabled={!ready || taking}
          onPress={() => void take()}
        />
        <Button
          variant="secondary"
          title={t('kyc.camera.cancel')}
          disabled={taking}
          onPress={onCancel}
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  content: { flex: 1, padding: spacing.xl, gap: spacing.lg },
});
