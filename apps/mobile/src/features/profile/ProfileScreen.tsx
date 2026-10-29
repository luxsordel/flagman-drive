import { useRouter } from 'expo-router';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Platform,
  type TextInputInstance,
} from 'react-native';

import { useMounted } from '../../hooks/useMounted';
import { languageNames, languages, type Language } from '../../i18n';
import { useTheme } from '../../theme/theme';
import { AppText } from '../../ui/AppText';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Screen } from '../../ui/Screen';
import { SegmentedControl } from '../../ui/SegmentedControl';
import { TextField } from '../../ui/TextField';
import { formatBelarusMobile } from '../auth/phone';
import { useAuthSession } from '../auth/session/AuthProvider';
import { DocumentsCard } from '../kyc/DocumentsCard';
import { canEditDocuments } from '../kyc/kycFlow';
import { useKycApplication, useKycStore } from '../kyc/KycProvider';
import { NotificationsCard } from '../notifications/NotificationsCard';
import { PaymentMethodsCard } from '../payments/PaymentMethodsCard';
import type { Profile } from './profileApi';
import type { ProfileError } from './profileErrors';
import { ProfileErrorMessage } from './ProfileErrorMessage';
import {
  checkDraft,
  draftFromProfile,
  formatDateInput,
  maxEmailLength,
  maxNameLength,
  type DraftErrors,
  type DraftField,
  type ProfileDraft,
} from './profileForm';
import { useProfile, useProfileStore } from './ProfileProvider';

// iOS has a number pad with no dot and no return key; its punctuation pad keeps the focus chain.
const dateKeyboard = Platform.OS === 'ios' ? 'numbers-and-punctuation' : 'number-pad';

/** The client's own details, the app language, the document status and signing out. */
export function ProfileScreen() {
  const { t } = useTranslation();
  const router = useRouter();
  const { colors } = useTheme();
  const store = useProfileStore();
  const { profile, loading, loadError, saving } = useProfile();

  return (
    <Screen>
      <AppText variant="title">{t('profile.title')}</AppText>
      {profile === null ? (
        loadError === null && <ActivityIndicator color={colors.primary} />
      ) : (
        <>
          <ProfileForm profile={profile} saving={saving} />
          <LanguagePicker profile={profile} saving={saving} />
        </>
      )}
      {/* Shown over stale data too: the client should know the profile may be out of date. */}
      <ProfileErrorMessage error={loadError} />
      {loadError !== null && (
        <Button
          variant="secondary"
          title={t('profile.retry')}
          disabled={loading}
          onPress={() => void store.load()}
        />
      )}
      <DocumentsCard />
      <PaymentMethodsCard />
      <NotificationsCard />
      <SignOutButton />
      <Button
        variant="secondary"
        title={t('profile.home')}
        onPress={() => {
          router.dismissTo('/');
        }}
      />
    </Screen>
  );
}

interface SectionProps {
  profile: Profile;
  /** Any save, of the form or the language: one at a time, so answers cannot cross. */
  saving: boolean;
}

function ProfileForm({ profile, saving }: SectionProps) {
  const { t } = useTranslation();
  const store = useProfileStore();
  const kycStore = useKycStore();
  const { application } = useKycApplication();
  // Until the status loads the fields stay open: the server refuses a change all the same.
  const identityLocked = application !== null && !canEditDocuments(application.status);
  const mounted = useMounted();
  const [draft, setDraft] = useState<ProfileDraft>(() => draftFromProfile(profile));
  // Fresh data replaces the form only until the client starts typing, never their input.
  const [seed, setSeed] = useState(profile);
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<DraftErrors>({});
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<ProfileError | null>(null);
  const [saved, setSaved] = useState(false);
  const lastNameRef = useRef<TextInputInstance>(null);
  const birthDateRef = useRef<TextInputInstance>(null);
  const emailRef = useRef<TextInputInstance>(null);

  if (profile !== seed) {
    setSeed(profile);
    if (!dirty) setDraft(draftFromProfile(profile));
  }

  const edit = (field: DraftField) => (value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setDirty(true);
    // The mistake stays until the client touches the field, rather than flickering per key.
    setErrors((current) => ({ ...current, [field]: undefined }));
    setSaved(false);
  };
  const fieldError = (field: DraftField): string | undefined => {
    const error = errors[field];
    if (error === undefined) return undefined;
    if (error !== 'tooLong') return t(`profile.fieldErrors.${error}`);
    return t('profile.fieldErrors.tooLong', {
      max: field === 'email' ? maxEmailLength : maxNameLength,
    });
  };

  async function save() {
    // The store, not the prop: a second tap can come before the screen re-renders.
    if (store.getState().saving) return;
    setSaveError(null);
    setSaved(false);
    const checked = checkDraft(draft, new Date(), { identityLocked });
    if (!checked.ok) {
      setErrors(checked.errors);
      return;
    }
    setBusy(true);
    const result = await store.save(checked.update);
    if (!mounted.current) return;
    setBusy(false);
    if (!result.ok) {
      // Belongs next to the date, like the checks the form makes itself.
      if (result.error.kind === 'birthDateInvalid') setErrors({ birthDate: 'dateRejected' });
      else setSaveError(result.error);
      // The documents went for review meanwhile: the status locks the fields it names.
      if (result.error.kind === 'locked') void kycStore.load();
      return;
    }
    // What the API kept, e.g. trimmed, rather than what the client typed.
    setDraft(draftFromProfile(result.profile));
    setDirty(false);
    setSaved(true);
    AccessibilityInfo.announceForAccessibility(t('profile.saved'));
  }

  return (
    <>
      <TextField
        label={t('profile.firstName')}
        value={draft.firstName}
        onChangeText={edit('firstName')}
        editable={!saving && !identityLocked}
        error={fieldError('firstName')}
        autoCapitalize="words"
        autoCorrect={false}
        autoComplete="given-name"
        textContentType="givenName"
        returnKeyType="next"
        submitBehavior="submit"
        onSubmitEditing={() => lastNameRef.current?.focus()}
      />
      <TextField
        ref={lastNameRef}
        label={t('profile.lastName')}
        value={draft.lastName}
        onChangeText={edit('lastName')}
        editable={!saving && !identityLocked}
        error={fieldError('lastName')}
        autoCapitalize="words"
        autoCorrect={false}
        autoComplete="family-name"
        textContentType="familyName"
        returnKeyType="next"
        submitBehavior="submit"
        onSubmitEditing={() => birthDateRef.current?.focus()}
      />
      <TextField
        ref={birthDateRef}
        label={t('profile.birthDate')}
        hint={t('profile.birthDateHint')}
        placeholder={t('profile.birthDatePlaceholder')}
        value={draft.birthDate}
        editable={!saving && !identityLocked}
        onChangeText={(text) => {
          edit('birthDate')(formatDateInput(text, draft.birthDate));
        }}
        error={fieldError('birthDate')}
        keyboardType={dateKeyboard}
        autoComplete="birthdate-full"
        returnKeyType="next"
        submitBehavior="submit"
        onSubmitEditing={() => emailRef.current?.focus()}
      />
      {identityLocked && (
        <AppText variant="caption" tone="muted">
          {t('profile.identityLocked')}
        </AppText>
      )}
      <TextField
        ref={emailRef}
        label={t('profile.email')}
        hint={t('profile.emailHint')}
        value={draft.email}
        onChangeText={edit('email')}
        editable={!saving}
        error={fieldError('email')}
        keyboardType="email-address"
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="email"
        textContentType="emailAddress"
        returnKeyType="done"
        onSubmitEditing={() => void save()}
      />
      <Card>
        <AppText variant="label">{t('profile.phone')}</AppText>
        <AppText>{formatBelarusMobile(profile.phone)}</AppText>
        <AppText variant="caption" tone="muted">
          {t('profile.phoneHint')}
        </AppText>
      </Card>
      <Button
        title={busy ? t('profile.saving') : t('profile.save')}
        disabled={saving}
        onPress={() => void save()}
      />
      <ProfileErrorMessage error={saveError} />
      {saved && <AppText>{t('profile.saved')}</AppText>}
    </>
  );
}

function LanguagePicker({ profile, saving }: SectionProps) {
  const { t } = useTranslation();
  const store = useProfileStore();
  const mounted = useMounted();
  const [choosing, setChoosing] = useState<Language | null>(null);
  const [error, setError] = useState<ProfileError | null>(null);

  async function choose(language: Language) {
    // As in the form: a second tap can come before `disabled` reaches the screen.
    if (store.getState().saving) return;
    setChoosing(language);
    setError(null);
    // The interface switches once the profile keeps the choice (see followProfileLanguage).
    const result = await store.save({ language });
    if (!mounted.current) return;
    setChoosing(null);
    if (!result.ok) setError(result.error);
  }

  return (
    <>
      <SegmentedControl
        label={t('profile.language')}
        options={languages.map((language) => ({
          value: language,
          label: languageNames[language],
          language,
        }))}
        value={choosing ?? profile.language}
        disabled={saving}
        onChange={(language) => void choose(language)}
      />
      <ProfileErrorMessage error={error} />
    </>
  );
}

function SignOutButton() {
  const { t } = useTranslation();
  const session = useAuthSession();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="secondary"
      title={t('auth.signOut')}
      disabled={busy}
      onPress={() => {
        setBusy(true);
        void session.signOut();
      }}
    />
  );
}
