import { OpenSans_400Regular } from '@expo-google-fonts/open-sans/400Regular';
import { OpenSans_600SemiBold } from '@expo-google-fonts/open-sans/600SemiBold';
import { OpenSans_700Bold } from '@expo-google-fonts/open-sans/700Bold';
import { Oswald_500Medium } from '@expo-google-fonts/oswald/500Medium';
import { Oswald_600SemiBold } from '@expo-google-fonts/oswald/600SemiBold';
import { Oswald_700Bold } from '@expo-google-fonts/oswald/700Bold';
import { fontFamily, type fontWeight } from '@flagman/design-tokens';

export type HeadingWeight = (typeof fontWeight.heading)[number];
export type BodyWeight = (typeof fontWeight.body)[number];

// Android selects a custom font by family name and ignores `fontWeight`, so each weight is
// registered as its own family (e.g. "Oswald-700") and picked explicitly.
export const headingFont = (weight: HeadingWeight): string =>
  `${fontFamily.heading}-${String(weight)}`;
export const bodyFont = (weight: BodyWeight): string => `${fontFamily.body}-${String(weight)}`;

// Per-weight subpath imports keep unused weights out of the bundle.
export const fontAssets: Readonly<Record<string, number>> = {
  [headingFont(500)]: Oswald_500Medium,
  [headingFont(600)]: Oswald_600SemiBold,
  [headingFont(700)]: Oswald_700Bold,
  [bodyFont(400)]: OpenSans_400Regular,
  [bodyFont(600)]: OpenSans_600SemiBold,
  [bodyFont(700)]: OpenSans_700Bold,
};
