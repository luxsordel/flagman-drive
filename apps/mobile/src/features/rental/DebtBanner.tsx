import { radius, spacing } from '@flagman/design-tokens';
import { useTranslation } from 'react-i18next';
import { StyleSheet, View } from 'react-native';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { formatKopecks } from '../map/mapFormat';
import type { Debt, Fine } from './rentalApi';

/**
 * On the map while an earlier rental or a fine is unpaid: no new rental starts before it is. A
 * rental's debt comes first, as its receipt is where it is paid; fines are paid in «My fines».
 */
export function DebtBanner({
  debts,
  unpaidFines,
  onOpen,
  onOpenFines,
}: {
  debts: Debt[];
  unpaidFines: Fine[];
  onOpen: (id: string) => void;
  onOpenFines: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { colors } = useTheme();
  const [first] = debts;
  const money = (amounts: { amount: number }[]) =>
    formatKopecks(
      amounts.reduce((sum, item) => sum + item.amount, 0),
      i18n.language,
    );
  let text: string;
  let open: () => void;
  if (first !== undefined) {
    text = t('rental.debt.banner', { amount: money(debts) });
    open = () => {
      onOpen(first.rental_id);
    };
  } else if (unpaidFines.length > 0) {
    text = t('rental.debt.fines', { amount: money(unpaidFines) });
    open = onOpenFines;
  } else {
    return null;
  }
  return (
    <View style={[styles.sheet, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <AppText tone="danger" accessibilityRole="alert">
        {text}
      </AppText>
      <Button title={t('rental.debt.open')} onPress={open} />
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
