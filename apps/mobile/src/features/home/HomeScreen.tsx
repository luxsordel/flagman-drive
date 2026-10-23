import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Screen } from '../../ui/Screen';
import { DocumentsCard } from '../kyc/DocumentsCard';

/** Placeholder for signed-in clients until the map arrives. */
export function HomeScreen() {
  const { t } = useTranslation();
  const router = useRouter();

  return (
    <Screen>
      <AppText variant="title">{t('home.title')}</AppText>
      <AppText tone="muted">{t('home.subtitle')}</AppText>
      <DocumentsCard />
      <Button
        variant="secondary"
        title={t('home.profile')}
        onPress={() => {
          router.push('/profile');
        }}
      />
    </Screen>
  );
}
