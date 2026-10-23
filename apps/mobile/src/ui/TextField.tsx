import { fontSize, minTapTarget, radius, spacing, tabularNumbers } from '@flagman/design-tokens';
import { useId, type Ref } from 'react';
import {
  StyleSheet,
  TextInput,
  View,
  type TextInputInstance,
  type TextInputProps,
} from 'react-native';

import { bodyFont } from '../theme/fonts';
import { useTheme } from '../theme/theme';
import { AppText } from './AppText';

export interface TextFieldProps extends Omit<TextInputProps, 'style'> {
  label: string;
  hint?: string;
  /** Fixed text before the input, e.g. a country code. */
  prefix?: string;
  /** Shown instead of the hint while the value is wrong; `undefined` when it is fine. */
  error?: string | undefined;
  /** To move the focus here from the previous field. */
  ref?: Ref<TextInputInstance>;
}

export function TextField({ label, hint, prefix, error, ref, ...inputProps }: TextFieldProps) {
  const { colors } = useTheme();
  const labelId = useId();
  return (
    <View style={styles.container}>
      <AppText variant="label" nativeID={labelId}>
        {label}
      </AppText>
      <View
        style={[
          styles.field,
          {
            backgroundColor: colors.surface,
            borderColor: error === undefined ? colors.border : colors.danger,
          },
        ]}
      >
        {prefix !== undefined && <AppText style={styles.prefix}>{prefix}</AppText>}
        <TextInput
          ref={ref}
          accessibilityLabel={label}
          accessibilityLabelledBy={labelId}
          placeholderTextColor={colors.inkMuted}
          style={[styles.input, { color: colors.ink }]}
          {...inputProps}
          // After the spread, so a caller's hint cannot hide the mistake: the screen reader
          // reads it on the field itself, not only once when it appears.
          {...(error === undefined ? {} : { accessibilityHint: error })}
        />
      </View>
      {error !== undefined ? (
        <AppText
          variant="caption"
          tone="danger"
          accessibilityRole="alert"
          accessibilityLiveRegion="polite"
        >
          {error}
        </AppText>
      ) : (
        hint !== undefined && (
          <AppText variant="caption" tone="muted">
            {hint}
          </AppText>
        )
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: spacing.sm },
  field: {
    minHeight: minTapTarget,
    borderWidth: 1,
    borderRadius: radius.field,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  prefix: { fontVariant: [...tabularNumbers.reactNative] },
  input: {
    flex: 1,
    minHeight: minTapTarget,
    fontFamily: bodyFont(400),
    fontSize: fontSize.md,
    fontVariant: [...tabularNumbers.reactNative],
  },
});
