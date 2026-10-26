import { spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { Suspense, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AppState, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { useKycApplication } from '../kyc/KycProvider';
import { CarCard } from './CarCard';
import { LazyCarMap } from './LazyCarMap';
import { useClientLocation } from './location';
import type { LatLon } from './mapConfig';
import { MapChip } from './MapChip';
import { mapNotice } from './mapNotice';
import { useMapState, useMapStore } from './MapProvider';
import { isExpoGo } from './platform';

/** The client's home screen: free cars and the zones where a rental may end. */
export function MapScreen() {
  const { colors } = useTheme();
  return (
    <View style={[styles.fill, { backgroundColor: colors.background }]}>
      {isExpoGo ? <ExpoGoNotice /> : <LiveMap />}
      <SafeAreaView style={styles.top} edges={['top']} pointerEvents="box-none">
        <TopBar />
      </SafeAreaView>
    </View>
  );
}

function TopBar() {
  const { t } = useTranslation();
  const router = useRouter();
  const { application } = useKycApplication();
  return (
    <View style={styles.bar} pointerEvents="box-none">
      <MapChip
        title={
          application === null
            ? t('map.documentsLoading')
            : t('map.documents', { status: t(`kyc.statusShort.${application.status}`) })
        }
        onPress={() => {
          router.push('/kyc');
        }}
      />
      <MapChip
        title={t('map.profile')}
        onPress={() => {
          router.push('/profile');
        }}
      />
    </View>
  );
}

function ExpoGoNotice() {
  const { t } = useTranslation();
  return (
    <View style={[styles.fill, styles.center, styles.notice]}>
      <Card>
        <AppText variant="label">{t('map.expoGo.title')}</AppText>
        <AppText tone="muted">{t('map.expoGo.body')}</AppText>
      </Card>
    </View>
  );
}

function LiveMap() {
  const { t } = useTranslation();
  const theme = useTheme();
  const store = useMapStore();
  const state = useMapState();
  const { status, location } = useClientLocation();
  // The camera follows the client's location once, when it first becomes known; later fixes
  // only update the distances, so the map does not jump away from where the client panned it.
  const [focus, setFocus] = useState<LatLon | null>(null);
  if (focus === null && location !== null) setFocus(location);

  useEffect(() => {
    store.setLocation(location);
  }, [store, location]);

  useEffect(() => {
    store.start();
    // Nothing to refresh for while the app is in the background.
    const subscription = AppState.addEventListener('change', (appState) => {
      if (appState === 'active') store.start();
      else store.stop();
    });
    return () => {
      subscription.remove();
      store.stop();
      store.clearSelection();
    };
  }, [store]);

  const notice = mapNotice(state, status === 'denied');

  return (
    <>
      <Suspense fallback={null}>
        <LazyCarMap
          cars={state.cars}
          zones={state.zones}
          selectedId={state.selected?.id ?? null}
          focus={focus}
          showUserLocation={status === 'granted'}
          palette={theme.colors}
          label={t('map.label')}
          onAreaChange={(area) => {
            store.showArea(area);
          }}
          onCarPress={(id) => {
            store.select(id);
          }}
          onMapPress={() => {
            store.clearSelection();
          }}
        />
      </Suspense>
      <SafeAreaView style={styles.bottom} edges={['bottom']} pointerEvents="box-none">
        {state.selected !== null ? (
          <CarCard
            selected={state.selected}
            onClose={() => {
              store.clearSelection();
            }}
          />
        ) : notice !== null ? (
          <View style={styles.notice}>
            <Card>
              <AppText
                tone={notice.kind === 'error' ? 'danger' : 'muted'}
                accessibilityRole={notice.kind === 'error' ? 'alert' : 'text'}
                accessibilityLiveRegion="polite"
              >
                {t(notice.text)}
              </AppText>
              {notice.kind === 'error' ? (
                <Button
                  variant="secondary"
                  title={t('map.retry')}
                  onPress={() => {
                    store.refresh();
                  }}
                />
              ) : null}
            </Card>
          </View>
        ) : null}
      </SafeAreaView>
    </>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  top: { position: 'absolute', top: 0, left: 0, right: 0 },
  bar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: spacing.sm,
    padding: spacing.lg,
  },
  // Leaves the map's attribution and logo in the corners visible under the card.
  bottom: { position: 'absolute', bottom: 0, left: 0, right: 0, paddingBottom: spacing.xxl },
  center: { justifyContent: 'center' },
  notice: { padding: spacing.lg },
});
