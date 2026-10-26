import { ThemeProvider, Stack } from 'expo-router';
import { useFonts } from 'expo-font';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, createAppAuthSession } from '../features/auth/session/AuthProvider';
import { createAppKycStore, KycProvider } from '../features/kyc/KycProvider';
import { createAppMapStore, MapProvider } from '../features/map/MapProvider';
import { createAppProfileStore, ProfileProvider } from '../features/profile/ProfileProvider';
import { initI18n } from '../i18n';
import { fontAssets } from '../theme/fonts';
import { navigationTheme, useTheme } from '../theme/theme';

initI18n();
void SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const theme = useTheme();
  const [session] = useState(createAppAuthSession);
  const [kycStore] = useState(() => createAppKycStore(session));
  const [profileStore] = useState(() => createAppProfileStore(session));
  const [mapStore] = useState(() => createAppMapStore(session));
  const status = useSyncExternalStore(session.subscribe, session.getStatus);
  const [fontsLoaded, fontError] = useFonts(fontAssets);
  // A failed font load falls back to system fonts rather than leaving the splash up forever.
  // The splash also covers session restore, so a signed-in user never flashes the sign-in screen.
  const ready = (fontsLoaded || fontError !== null) && status !== 'restoring';

  useEffect(() => {
    void session.restore();
  }, [session]);

  useEffect(() => {
    if (ready) void SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) return null;

  const signedIn = status === 'signedIn';
  return (
    <SafeAreaProvider>
      <ThemeProvider value={navigationTheme(theme)}>
        <AuthProvider session={session}>
          <KycProvider store={kycStore}>
            <ProfileProvider store={profileStore}>
              <MapProvider store={mapStore}>
                <StatusBar style="auto" />
                <Stack screenOptions={{ headerShown: false }}>
                  <Stack.Protected guard={signedIn}>
                    <Stack.Screen name="index" />
                    <Stack.Screen name="kyc/index" />
                    <Stack.Screen name="kyc/upload" />
                    <Stack.Screen name="profile" />
                  </Stack.Protected>
                  <Stack.Protected guard={!signedIn}>
                    <Stack.Screen name="sign-in" />
                    <Stack.Screen name="code" />
                  </Stack.Protected>
                </Stack>
              </MapProvider>
            </ProfileProvider>
          </KycProvider>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
