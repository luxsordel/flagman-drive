import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { useAuthSession } from '../auth/session/AuthProvider';

/** Placeholder for signed-in clients until the map and profile screens arrive (#37). */
export function HomeScreen() {
  const { t } = useTranslation();
  const session = useAuthSession();
  const [busy, setBusy] = useState(false);

  return (
    <Screen>
      <AppText variant="title">{t('home.title')}</AppText>
      <AppText tone="muted">{t('home.subtitle')}</AppText>
      <Button
        variant="secondary"
        title={t('auth.signOut')}
        disabled={busy}
        onPress={() => {
          setBusy(true);
          void session.signOut();
        }}
      />
    </Screen>
  );
}
