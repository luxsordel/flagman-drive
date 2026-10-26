import { spacing } from '@flagman/design-tokens';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { useMounted } from '../../hooks/useMounted';
import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { bindCard, type BindingOutcome } from './cardBinding';
import type { PaymentCard, Result } from './paymentsApi';
import type { PaymentsError } from './paymentsErrors';
import { usePayments } from './PaymentsProvider';

/** The client's cards: bind one on the provider's page, pick the default one, remove one. */
export function PaymentMethodsScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { colors } = useTheme();
  const deps = usePayments();
  const mounted = useMounted();
  const [cards, setCards] = useState<PaymentCard[] | null>(null);
  const [loadError, setLoadError] = useState<PaymentsError | null>(null);
  const [binding, setBinding] = useState(false);
  const [outcome, setOutcome] = useState<BindingOutcome | null>(null);
  // A second tap can come before the button re-renders disabled: it must not open a second page.
  const bindingRef = useRef(false);

  const show = (result: Result<PaymentCard[]>) => {
    if (result.ok) setCards(result.value);
    else setLoadError(result.error);
  };

  useEffect(() => {
    let active = true;
    void deps.api.listCards().then((result) => {
      if (!active) return;
      if (result.ok) setCards(result.value);
      else setLoadError(result.error);
    });
    return () => {
      active = false;
    };
  }, [deps]);

  async function load() {
    setLoadError(null);
    const result = await deps.api.listCards();
    if (mounted.current) show(result);
  }

  async function bind() {
    if (bindingRef.current) return;
    bindingRef.current = true;
    setBinding(true);
    setOutcome(null);
    const result = await bindCard(deps);
    bindingRef.current = false;
    if (!mounted.current) return;
    setBinding(false);
    setOutcome(result);
    // Also after `pending`: the provider may have reported by the time the list comes back.
    if (result.kind !== 'error') await load();
  }

  return (
    <Screen>
      <AppText variant="title">{t('payments.title')}</AppText>
      <AppText tone="muted">{t('payments.hint')}</AppText>
      {cards === null && loadError === null && <ActivityIndicator color={colors.primary} />}
      {loadError !== null && (
        <>
          <PaymentsErrorMessage error={loadError} />
          <Button variant="secondary" title={t('payments.retry')} onPress={() => void load()} />
        </>
      )}
      {cards !== null && cards.length === 0 && (
        <AppText tone="muted">{t('payments.empty')}</AppText>
      )}
      {cards?.map((card) => (
        // Keyed by card: a removal confirmed for one card never applies to another.
        <CardItem key={card.id} card={card} onChanged={() => void load()} />
      ))}
      <Button
        title={binding ? t('payments.binding') : t('payments.bind')}
        disabled={binding}
        onPress={() => void bind()}
      />
      <BindingOutcomeMessage outcome={outcome} />
      <Button
        variant="secondary"
        title={t('payments.back')}
        onPress={() => {
          router.back();
        }}
      />
    </Screen>
  );
}

function CardItem({ card, onChanged }: { card: PaymentCard; onChanged: () => void }) {
  const { t } = useTranslation();
  const { api } = usePayments();
  const mounted = useMounted();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PaymentsError | null>(null);
  const busyRef = useRef(false);
  const name = t('payments.card.name', {
    brand: t(`payments.brands.${card.brand}`),
    last4: card.last4,
  });
  const expiry = `${String(card.exp_month).padStart(2, '0')}/${String(card.exp_year % 100).padStart(2, '0')}`;

  async function run(action: () => Promise<{ ok: boolean; error?: PaymentsError }>) {
    // The ref, not the state: a second tap can come before the buttons re-render disabled.
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    const result = await action();
    busyRef.current = false;
    if (!mounted.current) return;
    setBusy(false);
    setConfirming(false);
    if (result.ok) onChanged();
    // Removed meanwhile, e.g. on another phone: the fresh list says it best.
    else if (result.error?.kind === 'notFound') onChanged();
    else setError(result.error ?? { kind: 'unknown' });
  }

  return (
    <Card>
      <AppText variant="label">{name}</AppText>
      <AppText tone={card.expired ? 'danger' : 'muted'}>
        {card.expired ? t('payments.card.expired') : t('payments.card.validThrough', { expiry })}
      </AppText>
      {card.is_default && <AppText>{t('payments.card.default')}</AppText>}
      <PaymentsErrorMessage error={error} />
      {confirming ? (
        <View
          style={styles.actions}
          accessibilityLabel={t('payments.card.removeConfirm', { name })}
        >
          <AppText>{t('payments.card.removeConfirm', { name })}</AppText>
          <Button
            title={t('payments.card.removeYes')}
            disabled={busy}
            onPress={() => void run(() => api.removeCard(card.id))}
          />
          <Button
            variant="secondary"
            title={t('payments.card.removeNo')}
            disabled={busy}
            onPress={() => {
              setConfirming(false);
            }}
          />
        </View>
      ) : (
        <View style={styles.actions}>
          {/* An expired card cannot take the payments: the server refuses it as well. */}
          {!card.is_default && !card.expired && (
            <Button
              variant="secondary"
              title={t('payments.card.makeDefault')}
              disabled={busy}
              onPress={() => void run(() => api.setDefaultCard(card.id))}
            />
          )}
          <Button
            variant="secondary"
            title={t('payments.card.remove')}
            disabled={busy}
            onPress={() => {
              setConfirming(true);
            }}
          />
        </View>
      )}
    </Card>
  );
}

function BindingOutcomeMessage({ outcome }: { outcome: BindingOutcome | null }) {
  const { t } = useTranslation();
  if (outcome === null) return null;
  if (outcome.kind === 'error') return <PaymentsErrorMessage error={outcome.error} />;
  const failed = outcome.kind === 'declined' || outcome.kind === 'expired';
  return (
    <AppText
      tone={failed ? 'danger' : 'default'}
      accessibilityRole={failed ? 'alert' : 'text'}
      accessibilityLiveRegion="polite"
    >
      {t(`payments.outcome.${outcome.kind}`)}
    </AppText>
  );
}

function PaymentsErrorMessage({ error }: { error: PaymentsError | null }) {
  const { t } = useTranslation();
  if (error === null) return null;
  return (
    <AppText tone="danger" accessibilityRole="alert" accessibilityLiveRegion="polite">
      {error.kind === 'rateLimited' && error.retryAfterSeconds !== undefined
        ? t('payments.errors.rateLimitedFor', { minutes: Math.ceil(error.retryAfterSeconds / 60) })
        : t(`payments.errors.${error.kind}`)}
    </AppText>
  );
}

const styles = StyleSheet.create({
  actions: { gap: spacing.sm },
});
