import { spacing } from '@flagman/design-tokens';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { TextField } from '../../ui/TextField';
import { belarusCountryCode, parseBelarusMobile } from './phone';

/** Phone sign-in stub: no API call until the identity module and SMS port exist. */
export function LoginScreen() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const [phone, setPhone] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const isValid = parseBelarusMobile(phone) !== null;

  return (
    <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.background }]}>
      <KeyboardAvoidingView
        style={styles.safeArea}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <AppText variant="title">{t('auth.login.title')}</AppText>
          <AppText>{t('auth.login.subtitle')}</AppText>
          <TextField
            label={t('auth.login.phoneLabel')}
            hint={t('auth.login.phoneHint')}
            prefix={belarusCountryCode}
            value={phone}
            onChangeText={(value) => {
              setPhone(value);
              setSubmitted(false);
            }}
            keyboardType="phone-pad"
            autoComplete="tel"
            textContentType="telephoneNumber"
            maxLength={20}
            testID="login-phone"
          />
          <Button
            title={t('auth.login.submit')}
            disabled={!isValid}
            onPress={() => {
              setSubmitted(true);
            }}
          />
          {submitted && (
            <AppText accessibilityLiveRegion="polite">{t('auth.login.notAvailable')}</AppText>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { flexGrow: 1, justifyContent: 'center', padding: spacing.xl, gap: spacing.lg },
});
