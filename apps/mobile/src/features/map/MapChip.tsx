import { fontSize, minTapTarget, radius, spacing } from '@flagman/design-tokens';
import { Pressable, StyleSheet, Text } from 'react-native';

import { bodyFont } from '../../theme/fonts';
import { useTheme } from '../../theme/theme';

/** A small button floating over the map: opaque, unlike Button's secondary look. */
export function MapChip({ title, onPress }: { title: string; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.chip,
        {
          backgroundColor: colors.surface,
          borderColor: pressed ? colors.primary : colors.border,
        },
      ]}
    >
      <Text style={[styles.title, { color: colors.primary }]} numberOfLines={1}>
        {title}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    minHeight: minTapTarget,
    borderWidth: 1,
    borderRadius: radius.button,
    paddingHorizontal: spacing.lg,
    justifyContent: 'center',
    flexShrink: 1,
  },
  title: { fontFamily: bodyFont(600), fontSize: fontSize.sm },
});
