import { darkPalette, lightPalette } from './colors';

export { colorNames, darkPalette, lightPalette } from './colors';
export type { ColorName, Palette } from './colors';
export { cssStaticVariables, cssVariables } from './css';
export { minTapTarget, radius, spacing } from './layout';
export { fontFamily, fontSize, fontWeight, tabularNumbers } from './typography';

export type ThemeName = 'light' | 'dark';

export const themes = { light: lightPalette, dark: darkPalette } as const;
