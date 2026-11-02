import { radius, spacing } from '@flagman/design-tokens';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { formatKopecks } from '../map/mapFormat';
import type { Debt } from './rentalApi';

/** On the map while an earlier rental is unpaid: no new one starts before it is. */
export function DebtBanner({ debts, onOpen }: { debts: Debt[]; onOpen: (id: string) => void }) {
  const { t, i18n } = useTranslation();
  const { colors } = useTheme();
  const [first] = debts;
  if (first === undefined) return null;
  const total = debts.reduce((sum, debt) => sum + debt.amount, 0);
  return (
    <View style={[styles.sheet, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <AppText tone="danger" accessibilityRole="alert">
        {t('rental.debt.banner', { amount: formatKopecks(total, i18n.language) })}
      </AppText>
      <Button
        title={t('rental.debt.open')}
        onPress={() => {
          onOpen(first.rental_id);
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  // The same sheet as the rental banner it stands in for.
  sheet: {
    borderWidth: 1,
    borderRadius: radius.sheet,
    marginHorizontal: spacing.lg,
    padding: spacing.xl,
    gap: spacing.md,
  },
});
