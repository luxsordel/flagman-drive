import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { formatKopecks } from '../map/mapFormat';
import type { Rental } from './rentalApi';
import { useRentalState, useRentalStore } from './RentalProvider';
import type { RentalAction } from './rentalStore';

/** The receipt of an ended rental and where its money stands: paid, being paid, or a debt. */
export function ReceiptView({ rental, pending }: { rental: Rental; pending: RentalAction | null }) {
  const { t, i18n } = useTranslation();
  const { receipt } = rental;
  if (receipt === null) return null;
  const money = (kopecks: number) => formatKopecks(kopecks, i18n.language);
  const issued = new Date(receipt.issued_at).toLocaleString(i18n.language);

  return (
    <Card>
      <AppText variant="label">{t('rental.receipt.title')}</AppText>
      {receipt.cost.lines.map((line) => (
        <AppText key={line.kind}>
          {t(`rental.receipt.lines.${line.kind}`)}:{' '}
          {t('rental.receipt.line', { minutes: line.minutes, price: money(line.per_minute) })} ={' '}
          {money(line.amount)}
        </AppText>
      ))}
      {receipt.cost.lines
        .filter((line) => line.free_minutes > 0)
        .map((line) => (
          <AppText key={`${line.kind}-free`} tone="muted">
            {t('rental.receipt.free', { minutes: line.free_minutes })}
          </AppText>
        ))}
      <AppText variant="title">
        {t('rental.receipt.total', { amount: money(receipt.cost.total) })}
      </AppText>
      <AppText tone="muted">
        {receipt.card_last4 !== null
          ? t('rental.receipt.card', {
              brand: (receipt.card_brand ?? '').toUpperCase(),
              last4: receipt.card_last4,
            })
          : t('rental.receipt.noCard')}
      </AppText>
      <AppText variant="caption" tone="muted">
        {t('rental.receipt.issued', { time: issued })}
      </AppText>
      <ChargeStatus rental={rental} pending={pending} />
    </Card>
  );
}

function ChargeStatus({ rental, pending }: { rental: Rental; pending: RentalAction | null }) {
  const { t, i18n } = useTranslation();
  const router = useRouter();
  const store = useRentalStore();
  const { debts } = useRentalState();
  switch (rental.charge_status) {
    case 'paid':
      return <AppText variant="label">{t('rental.receipt.paid')}</AppText>;
    case 'pending':
      return <AppText tone="muted">{t('rental.receipt.pending')}</AppText>;
    case 'failed': {
      // The debt's own amount, less what was taken from the hold; the receipt's total until known.
      const owed = debts.find((debt) => debt.rental_id === rental.id)?.amount ?? rental.cost.total;
      return (
        <>
          <AppText tone="danger" accessibilityRole="alert">
            {t('rental.receipt.failed', { amount: formatKopecks(owed, i18n.language) })}
          </AppText>
          <AppText tone="muted">{t('rental.receipt.failedHint')}</AppText>
          <Button
            title={pending === 'pay' ? t('rental.receipt.paying') : t('rental.receipt.pay')}
            disabled={pending !== null}
            onPress={() => void store.payDebt()}
          />
          <Button
            variant="secondary"
            title={t('rental.receipt.cards')}
            onPress={() => {
              router.push('/payment-methods');
            }}
          />
        </>
      );
    }
    default:
      return null;
  }
}
