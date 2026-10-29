import { ThemeProvider, Stack } from 'expo-router';
import { useFonts } from 'expo-font';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, createAppAuthSession } from '../features/auth/session/AuthProvider';
import { createAppKycStore, KycProvider } from '../features/kyc/KycProvider';
import { createAppMapStore, MapProvider } from '../features/map/MapProvider';
import { createAppNotifications } from '../features/notifications/appNotifications';
import { NotificationsProvider } from '../features/notifications/NotificationsContext';
import { PushListeners } from '../features/notifications/PushListeners';
import { createAppPayments, PaymentsProvider } from '../features/payments/PaymentsProvider';
import { createAppProfileStore, ProfileProvider } from '../features/profile/ProfileProvider';
import { createAppRentalStore, RentalProvider } from '../features/rental/RentalProvider';
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
  const [rentalStore] = useState(() => createAppRentalStore(session));
  const [payments] = useState(() => createAppPayments(session));
  const [notifications] = useState(() => createAppNotifications(session));
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
                <RentalProvider store={rentalStore}>
                  <PaymentsProvider deps={payments}>
                    <NotificationsProvider deps={notifications}>
                      <StatusBar style="auto" />
                      <PushListeners session={session} deps={notifications} />
                      <Stack screenOptions={{ headerShown: false }}>
                        <Stack.Protected guard={signedIn}>
                          <Stack.Screen name="index" />
                          <Stack.Screen name="kyc/index" />
                          <Stack.Screen name="kyc/upload" />
                          <Stack.Screen name="profile" />
                          <Stack.Screen name="rental/index" />
                          <Stack.Screen name="rental/inspection" />
                          <Stack.Screen name="payment-methods" />
                          <Stack.Screen name="notification-settings" />
                        </Stack.Protected>
                        <Stack.Protected guard={!signedIn}>
                          <Stack.Screen name="sign-in" />
                          <Stack.Screen name="code" />
                        </Stack.Protected>
                      </Stack>
                    </NotificationsProvider>
                  </PaymentsProvider>
                </RentalProvider>
              </MapProvider>
            </ProfileProvider>
          </KycProvider>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
