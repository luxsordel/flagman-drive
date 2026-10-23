import { radius, spacing } from '@flagman/design-tokens';
import { StyleSheet, View } from 'react-native';

import { useTheme } from '../theme/theme';

export interface ProgressBarProps {
  /** Share done, 0..1. */
  value: number;
  accessibilityLabel: string;
}

export function ProgressBar({ value, accessibilityLabel }: ProgressBarProps) {
  const { colors } = useTheme();
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <View
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={accessibilityLabel}
      accessibilityValue={{ min: 0, max: 100, now: percent }}
      style={[styles.track, { backgroundColor: colors.border }]}
    >
      <View
        style={[styles.fill, { width: `${String(percent)}%`, backgroundColor: colors.primary }]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  track: { height: spacing.sm, borderRadius: radius.field, overflow: 'hidden' },
  fill: { height: '100%' },
});
