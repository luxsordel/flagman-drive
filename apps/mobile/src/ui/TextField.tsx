import { fontSize, minTapTarget, radius, spacing, tabularNumbers } from '@flagman/design-tokens';
import { useId } from 'react';
import { StyleSheet, TextInput, View, type TextInputProps } from 'react-native';

import { bodyFont } from '../theme/fonts';
import { useTheme } from '../theme/theme';
import { AppText } from './AppText';

export interface TextFieldProps extends Omit<TextInputProps, 'style'> {
  label: string;
  hint?: string;
  /** Fixed text before the input, e.g. a country code. */
  prefix?: string;
}

export function TextField({ label, hint, prefix, ...inputProps }: TextFieldProps) {
  const { colors } = useTheme();
  const labelId = useId();
  return (
    <View style={styles.container}>
      <AppText variant="label" nativeID={labelId}>
        {label}
      </AppText>
      <View style={[styles.field, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        {prefix !== undefined && <AppText style={styles.prefix}>{prefix}</AppText>}
        <TextInput
          accessibilityLabel={label}
          accessibilityLabelledBy={labelId}
          placeholderTextColor={colors.inkMuted}
          style={[styles.input, { color: colors.ink }]}
          {...inputProps}
        />
      </View>
      {hint !== undefined && (
        <AppText variant="caption" tone="muted">
          {hint}
        </AppText>
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
