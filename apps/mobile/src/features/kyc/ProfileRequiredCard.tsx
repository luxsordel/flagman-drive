import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';

/** Shown instead of a way to send the documents while the profile lacks what they are checked against. */
export function ProfileRequiredCard() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Card>
      <AppText variant="label">{t('kyc.profileRequired.title')}</AppText>
      <AppText>{t('kyc.profileRequired.text')}</AppText>
      <Button
        title={t('kyc.profileRequired.open')}
        onPress={() => {
          router.push('/profile');
        }}
      />
    </Card>
  );
}
