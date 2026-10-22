export const fontFamily = {
  heading: 'Oswald',
  body: 'Open Sans',
} as const;

/** Weights the app loads; anything else would fall back to a synthetic style. */
export const fontWeight = {
  heading: [500, 600, 700],
  body: [400, 600, 700],
} as const;

/** Type scale in px; 16 is the base size. */
export const fontSize = {
  xs: 12,
  sm: 14,
  md: 16,
  lg: 20,
  xl: 24,
  xxl: 32,
} as const;

/** Prices, timers and charge levels use tabular figures so digits do not jump while they change. */
export const tabularNumbers = {
  /** CSS `font-variant-numeric` (web, admin). */
  css: 'tabular-nums',
  /** React Native `fontVariant` (mobile). */
  reactNative: ['tabular-nums'],
} as const;
