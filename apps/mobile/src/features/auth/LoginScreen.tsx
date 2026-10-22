import { useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { TextField } from '../../ui/TextField';
import { AuthErrorMessage } from './AuthErrorMessage';
import { belarusCountryCode, parseBelarusMobile } from './phone';
import type { AuthError } from './session/authErrors';
import { useAuthSession } from './session/AuthProvider';

/** First sign-in step: the phone number to text a code to. */
export function LoginScreen() {
  const { t } = useTranslation();
  const session = useAuthSession();
  const router = useRouter();
  const [phone, setPhone] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AuthError | null>(null);
  const normalized = parseBelarusMobile(phone);

  async function submit(target: string) {
    setBusy(true);
    setError(null);
    const failure = await session.requestCode(target);
    setBusy(false);
    if (failure === null) router.push('/code');
    else setError(failure);
  }

  return (
    <Screen>
      <AppText variant="title">{t('auth.login.title')}</AppText>
      <AppText>{t('auth.login.subtitle')}</AppText>
      <TextField
        label={t('auth.login.phoneLabel')}
        hint={t('auth.login.phoneHint')}
        prefix={belarusCountryCode}
        value={phone}
        onChangeText={(value) => {
          setPhone(value);
          setError(null);
        }}
        keyboardType="phone-pad"
        autoComplete="tel"
        textContentType="telephoneNumber"
        maxLength={20}
        testID="login-phone"
      />
      <Button
        title={t('auth.login.submit')}
        disabled={normalized === null || busy}
        onPress={() => {
          if (normalized !== null) void submit(normalized);
        }}
      />
      <AuthErrorMessage error={error} />
    </Screen>
  );
}
