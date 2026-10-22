import { Redirect, useRouter } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Platform } from 'react-native';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { TextField } from '../../ui/TextField';
import { AuthErrorMessage } from './AuthErrorMessage';
import { formatWait, useSecondsUntil } from './countdown';
import { formatBelarusMobile } from './phone';
import type { AuthError } from './session/authErrors';
import type { PendingSignIn } from './session/authSession';
import { useAuthSession, usePendingSignIn } from './session/AuthProvider';

const codeLength = 6;

/** Second sign-in step: the code from the SMS. */
export function CodeScreen() {
  const pending = usePendingSignIn();
  // The phone lives in memory only, so a web reload on this step starts over.
  if (pending === null) return <Redirect href="/sign-in" />;
  return <CodeForm pending={pending} />;
}

function CodeForm({ pending }: { pending: PendingSignIn }) {
  const { t } = useTranslation();
  const session = useAuthSession();
  const router = useRouter();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<AuthError | null>(null);

  // On success the session turns signed-in and the router leaves this screen by itself.
  async function verify(value: string) {
    setBusy(true);
    setError(null);
    const failure = await session.verifyCode(value);
    if (failure === null) return;
    setBusy(false);
    setError(failure);
    setCode('');
  }

  async function resend() {
    setBusy(true);
    setError(null);
    const failure = await session.requestCode(pending.phone);
    setBusy(false);
    setError(failure);
    setCode('');
  }

  return (
    <Screen>
      <AppText variant="title">{t('auth.code.title')}</AppText>
      <AppText>{t('auth.code.subtitle', { phone: formatBelarusMobile(pending.phone) })}</AppText>
      <TextField
        label={t('auth.code.codeLabel')}
        value={code}
        onChangeText={(value) => {
          const digits = value.replace(/\D/g, '').slice(0, codeLength);
          setCode(digits);
          setError(null);
          // SMS autofill pastes the whole code at once: no extra tap needed.
          if (digits.length === codeLength && !busy) void verify(digits);
        }}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete={Platform.OS === 'android' ? 'sms-otp' : 'one-time-code'}
        maxLength={codeLength}
        autoFocus
        editable={!busy}
        testID="code-input"
      />
      <Button
        title={t('auth.code.submit')}
        disabled={code.length !== codeLength || busy}
        onPress={() => void verify(code)}
      />
      <AuthErrorMessage error={error} />
      <ResendButton
        key={pending.resendAvailableAt}
        availableAt={pending.resendAvailableAt}
        disabled={busy}
        onPress={() => void resend()}
      />
      <Button
        variant="secondary"
        title={t('auth.code.changePhone')}
        onPress={() => {
          router.back();
        }}
      />
    </Screen>
  );
}

function ResendButton({
  availableAt,
  disabled,
  onPress,
}: {
  availableAt: number;
  disabled: boolean;
  onPress: () => void;
}) {
  const { t } = useTranslation();
  const secondsLeft = useSecondsUntil(availableAt);
  if (secondsLeft > 0) {
    return (
      <AppText variant="caption" tone="muted">
        {t('auth.code.resendIn', { time: formatWait(secondsLeft) })}
      </AppText>
    );
  }
  return (
    <Button
      variant="secondary"
      title={t('auth.code.resend')}
      disabled={disabled}
      onPress={onPress}
    />
  );
}
