import { radius, spacing } from '@flagman/design-tokens';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AppState,
  Image,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { TextField } from '../../ui/TextField';
import { pickPhoto, type PhotoSource } from '../kyc/photos';
import type { OutgoingMessage } from './chatStore';
import { useChatState, useSupport } from './SupportProvider';
import type { SupportMessage } from './supportApi';

/** Open chat (ADR-0023): live enough for a conversation, and only while it is on the screen. */
const pollEveryMs = 4000;

/** The chat with support; unsent messages stay visible and go by themselves when the network is back. */
export function SupportScreen() {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const router = useRouter();
  const { chat } = useSupport();
  const { messages, outgoing, closed, loaded, error } = useChatState();
  const { rentalId } = useLocalSearchParams<{ rentalId?: string }>();
  const [text, setText] = useState('');
  const [cameraDenied, setCameraDenied] = useState(false);

  useEffect(() => {
    void chat.poll();
    const timer = setInterval(() => {
      if (AppState.currentState === 'active') void chat.poll();
    }, pollEveryMs);
    return () => {
      clearInterval(timer);
    };
  }, [chat]);

  const submit = () => {
    const message = text.trim();
    if (!message) return;
    setText('');
    void chat.send(message, rentalId ?? null);
  };

  const attach = async (source: PhotoSource) => {
    setCameraDenied(false);
    const picked = await pickPhoto(source);
    if (picked.kind === 'cameraDenied') setCameraDenied(true);
    if (picked.kind === 'picked') void chat.sendPhoto(picked.uri);
  };

  return (
    <SafeAreaView style={[styles.fill, { backgroundColor: colors.background }]}>
      <KeyboardAvoidingView
        style={styles.fill}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.list} keyboardShouldPersistTaps="handled">
          <AppText variant="title">{t('support.title')}</AppText>
          {loaded && messages.length === 0 && outgoing.length === 0 && (
            <AppText tone="muted">{t('support.empty')}</AppText>
          )}
          {messages.map((message) => (
            <Bubble key={message.id} message={message} />
          ))}
          {outgoing.map((message) => (
            <Unsent key={message.id} message={message} />
          ))}
          {closed && <AppText tone="muted">{t('support.closed')}</AppText>}
        </ScrollView>
        <View style={styles.composer}>
          {error !== null && (
            <AppText tone="danger" accessibilityRole="alert">
              {t(`support.errors.${error.kind}`)}
            </AppText>
          )}
          {cameraDenied && (
            <AppText tone="danger" accessibilityRole="alert">
              {t('support.cameraDenied')}
            </AppText>
          )}
          <TextField
            label={t('support.messageLabel')}
            value={text}
            onChangeText={setText}
            multiline
            maxLength={2000}
          />
          <Button title={t('support.send')} disabled={!text.trim()} onPress={submit} />
          <View style={styles.row}>
            <View style={styles.cell}>
              <Button
                variant="secondary"
                title={t('support.camera')}
                onPress={() => void attach('camera')}
              />
            </View>
            <View style={styles.cell}>
              <Button
                variant="secondary"
                title={t('support.library')}
                onPress={() => void attach('library')}
              />
            </View>
          </View>
          <Button
            variant="secondary"
            title={t('support.back')}
            onPress={() => {
              router.back();
            }}
          />
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Bubble({ message }: { message: SupportMessage }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const mine = message.author === 'client';
  const author = mine
    ? t('support.you')
    : message.staff_name
      ? t('support.operatorNamed', { name: message.staff_name })
      : t('support.operator');
  return (
    <View
      style={[
        styles.bubble,
        mine ? styles.mine : styles.theirs,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <AppText variant="caption" tone="muted">
        {author}
      </AppText>
      {message.kind === 'photo' ? <Photo id={message.id} /> : <AppText>{message.text}</AppText>}
    </View>
  );
}

/** The link is asked for on a tap: it lives for minutes, and the chat is polled all the time. */
function Photo({ id }: { id: string }) {
  const { t } = useTranslation();
  const { api } = useSupport();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  if (url !== null) {
    return (
      <Image
        source={{ uri: url }}
        style={styles.photo}
        resizeMode="contain"
        accessibilityLabel={t('support.photo')}
      />
    );
  }
  return (
    <>
      <Button
        variant="secondary"
        title={t('support.showPhoto')}
        onPress={() => {
          setFailed(false);
          void api.photoUrl(id).then((link) => {
            if (link.ok) setUrl(link.value);
            else setFailed(true);
          });
        }}
      />
      {failed && <AppText tone="danger">{t('support.photoFailed')}</AppText>}
    </>
  );
}

function Unsent({ message }: { message: OutgoingMessage }) {
  const { t } = useTranslation();
  const { colors } = useTheme();
  const { chat } = useSupport();
  return (
    <View
      style={[
        styles.bubble,
        styles.mine,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <AppText>{message.text ?? t('support.photo')}</AppText>
      <AppText variant="caption" tone={message.state === 'refused' ? 'danger' : 'muted'}>
        {t(`support.outgoing.${message.state}`)}
      </AppText>
      {message.state !== 'sending' && (
        <Button
          variant="secondary"
          title={t('support.remove')}
          onPress={() => {
            chat.remove(message.id);
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  list: { flexGrow: 1, gap: spacing.md, padding: spacing.lg },
  composer: { gap: spacing.sm, paddingHorizontal: spacing.lg, paddingBottom: spacing.md },
  row: { flexDirection: 'row', gap: spacing.sm },
  cell: { flex: 1 },
  bubble: {
    maxWidth: '85%',
    gap: spacing.xs,
    padding: spacing.md,
    borderWidth: 1,
    borderRadius: radius.card,
  },
  mine: { alignSelf: 'flex-end' },
  theirs: { alignSelf: 'flex-start' },
  // The size of the chat column, whatever the shape of the photo.
  photo: { width: 240, height: 240 },
});
