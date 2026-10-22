import { fontSize, minTapTarget, radius, spacing } from '@flagman/design-tokens';
import { Pressable, StyleSheet, Text } from 'react-native';

import { bodyFont } from '../theme/fonts';
import { useTheme } from '../theme/theme';

export interface ButtonProps {
  title: string;
  onPress: () => void;
  disabled?: boolean;
}

export function Button({ title, onPress, disabled = false }: ButtonProps) {
  const { colors } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        { backgroundColor: pressed ? colors.primaryStrong : colors.primary },
        disabled && styles.disabled,
      ]}
    >
      <Text style={[styles.title, { color: colors.onPrimary }]}>{title}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: minTapTarget,
    borderRadius: radius.button,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  // Opacity instead of a separate token: the disabled state keeps the brand color recognisable.
  disabled: { opacity: 0.5 },
  title: { fontFamily: bodyFont(600), fontSize: fontSize.md },
});
