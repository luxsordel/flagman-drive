import { spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, AppState, StyleSheet, Switch, View } from 'react-native';

import { useMounted } from '../../hooks/useMounted';
import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { useNotifications } from './NotificationsContext';
import type { NotificationPreferences } from './notificationsApi';
import type { NotificationsError } from './notificationsErrors';
import { enablePush, syncPush, type PushState } from './pushRegistration';

type Category = NotificationPreferences['categories'][number];

/** Whether this phone gets pushes, and which kinds of notifications the client wants. */
export function NotificationSettingsScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { colors } = useTheme();
  const deps = useNotifications();
  const mounted = useMounted();
  const [preferences, setPreferences] = useState<NotificationPreferences | null>(null);
  const [loadError, setLoadError] = useState<NotificationsError | null>(null);

  useEffect(() => {
    let active = true;
    void deps.api.getPreferences().then((result) => {
      if (!active) return;
      if (result.ok) setPreferences(result.value);
      else setLoadError(result.error);
    });
    return () => {
      active = false;
    };
  }, [deps]);

  async function load() {
    setLoadError(null);
    const result = await deps.api.getPreferences();
    if (!mounted.current) return;
    if (result.ok) setPreferences(result.value);
    else setLoadError(result.error);
  }

  return (
    <Screen>
      <AppText variant="title">{t('notifications.title')}</AppText>
      <DevicePush />
      {preferences === null && loadError === null && <ActivityIndicator color={colors.primary} />}
      {loadError !== null && (
        <>
          <NotificationsErrorMessage error={loadError} />
          <Button
            variant="secondary"
            title={t('notifications.retry')}
            onPress={() => void load()}
          />
        </>
      )}
      {preferences !== null && (
        <Card>
          <AppText variant="label">{t('notifications.categoriesTitle')}</AppText>
          {preferences.categories.map((category) => (
            <CategoryRow key={category.category} category={category} onChanged={setPreferences} />
          ))}
        </Card>
      )}
      <Button
        variant="secondary"
        title={t('notifications.back')}
        onPress={() => {
          router.back();
        }}
      />
    </Screen>
  );
}

function DevicePush() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const { push, openSystemSettings } = useNotifications();
  const mounted = useMounted();
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const sync = () => {
      void syncPush(push).then((result) => {
        if (active) setState(result);
      });
    };
    sync();
    // The client may have allowed notifications in the system settings and come back.
    const subscription = AppState.addEventListener('change', (next) => {
      if (next === 'active') sync();
    });
    return () => {
      active = false;
      subscription.remove();
    };
  }, [push]);

  async function enable() {
    setBusy(true);
    const result = await enablePush(push);
    if (!mounted.current) return;
    setBusy(false);
    setState(result);
  }

  return (
    <Card>
      <AppText variant="label">{t('notifications.device.title')}</AppText>
      {state === null ? (
        <ActivityIndicator color={colors.primary} />
      ) : (
        <AppText tone={state === 'failed' ? 'danger' : 'muted'} accessibilityLiveRegion="polite">
          {t(`notifications.device.${state}`)}
        </AppText>
      )}
      {(state === 'askable' || state === 'failed') && (
        <Button
          title={state === 'askable' ? t('notifications.device.enable') : t('notifications.retry')}
          disabled={busy}
          onPress={() => void enable()}
        />
      )}
      {state === 'blocked' && (
        <Button
          variant="secondary"
          title={t('notifications.device.openSettings')}
          onPress={openSystemSettings}
        />
      )}
    </Card>
  );
}

function CategoryRow({
  category,
  onChanged,
}: {
  category: Category;
  onChanged: (preferences: NotificationPreferences) => void;
}) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const { api } = useNotifications();
  const mounted = useMounted();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<NotificationsError | null>(null);
  // A second toggle can come before the switch re-renders disabled.
  const savingRef = useRef(false);
  const name = t(`notifications.categories.${category.category}.name`);

  async function toggle(enabled: boolean) {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError(null);
    const result = await api.updatePreferences({ [category.category]: enabled });
    savingRef.current = false;
    if (!mounted.current) return;
    setSaving(false);
    if (result.ok) onChanged(result.value);
    else setError(result.error);
  }

  return (
    <View style={styles.row}>
      <View style={styles.text}>
        <AppText>{name}</AppText>
        <AppText tone="muted">
          {category.mandatory
            ? t('notifications.mandatory')
            : t(`notifications.categories.${category.category}.hint`)}
        </AppText>
        <NotificationsErrorMessage error={error} />
      </View>
      <Switch
        accessibilityLabel={name}
        value={category.enabled}
        disabled={category.mandatory || saving}
        accessibilityState={{ disabled: category.mandatory || saving, checked: category.enabled }}
        trackColor={{ true: colors.primary, false: colors.border }}
        onValueChange={(value) => void toggle(value)}
      />
    </View>
  );
}

function NotificationsErrorMessage({ error }: { error: NotificationsError | null }) {
  const { t } = useTranslation();
  if (error === null) return null;
  return (
    <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
      {error.kind === 'rateLimited' && error.retryAfterSeconds !== undefined
        ? t('notifications.errors.rateLimitedFor', {
            minutes: Math.ceil(error.retryAfterSeconds / 60),
          })
        : t(`notifications.errors.${error.kind}`)}
    </AppText>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  text: { flex: 1, gap: spacing.xs },
});
