import { fontSize } from '@flagman/design-tokens';
import { Text, type TextProps, type TextStyle } from 'react-native';

import { bodyFont, headingFont } from '../theme/fonts';
import { useTheme } from '../theme/theme';

type Variant = 'title' | 'body' | 'label' | 'caption';
type Tone = 'default' | 'muted' | 'danger';

const variants: Record<Variant, TextStyle> = {
  title: { fontFamily: headingFont(600), fontSize: fontSize.xxl },
  body: { fontFamily: bodyFont(400), fontSize: fontSize.md },
  label: { fontFamily: bodyFont(600), fontSize: fontSize.sm },
  caption: { fontFamily: bodyFont(400), fontSize: fontSize.xs },
};

export interface AppTextProps extends TextProps {
  variant?: Variant;
  tone?: Tone;
}

export function AppText({ variant = 'body', tone = 'default', style, ...rest }: AppTextProps) {
  const { colors } = useTheme();
  const color = { default: colors.ink, muted: colors.inkMuted, danger: colors.danger }[tone];
  return (
    <Text
      accessibilityRole={variant === 'title' ? 'header' : 'text'}
      style={[variants[variant], { color }, style]}
      {...rest}
    />
  );
}
