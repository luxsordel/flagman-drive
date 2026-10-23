import { fontSize, minTapTarget, radius, spacing } from '@flagman/design-tokens';
import { useId } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { bodyFont } from '../theme/fonts';
import { useTheme } from '../theme/theme';
import { AppText } from './AppText';
import { disabledOpacity } from './states';

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** BCP 47 tag when the label is in another language than the app, e.g. a language name. */
  language?: string;
}

export interface SegmentedControlProps<T extends string> {
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}

/** One choice out of a few short options, all visible at once, e.g. the app language. */
export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled = false,
}: SegmentedControlProps<T>) {
  const { colors } = useTheme();
  const labelId = useId();
  return (
    <View style={styles.container}>
      <AppText variant="label" nativeID={labelId}>
        {label}
      </AppText>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel={label}
        accessibilityLabelledBy={labelId}
        style={[styles.group, { borderColor: colors.border, backgroundColor: colors.surface }]}
      >
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <Pressable
              key={option.value}
              accessibilityRole="radio"
              accessibilityLabel={option.label}
              {...(option.language === undefined ? {} : { accessibilityLanguage: option.language })}
              accessibilityState={{ checked: selected, disabled }}
              disabled={disabled}
              onPress={() => {
                if (!selected) onChange(option.value);
              }}
              style={({ pressed }) => [
                styles.option,
                selected
                  ? { backgroundColor: colors.primary }
                  : pressed && { backgroundColor: colors.background },
                disabled && styles.disabled,
              ]}
            >
              <Text style={[styles.text, { color: selected ? colors.onPrimary : colors.ink }]}>
                {option.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: spacing.sm },
  group: {
    flexDirection: 'row',
    borderWidth: 1,
    borderRadius: radius.field,
    padding: spacing.xs,
    gap: spacing.xs,
  },
  option: {
    flex: 1,
    minHeight: minTapTarget,
    // Concentric corners: the inner radius is the outer one minus the gap between them.
    borderRadius: radius.field - spacing.xs,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
  },
  disabled: { opacity: disabledOpacity },
  text: { fontFamily: bodyFont(600), fontSize: fontSize.md },
});
