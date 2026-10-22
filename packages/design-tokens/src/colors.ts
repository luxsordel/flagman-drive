/** Values are from the color table in docs/07-design-system.md; change both together. */
export const colorNames = [
  'primary',
  'primaryStrong',
  'ink',
  'inkMuted',
  'surface',
  'background',
  'border',
  'success',
  'warning',
  'danger',
] as const;

export type ColorName = (typeof colorNames)[number];

export type Palette = Readonly<Record<ColorName, string>>;

export const lightPalette: Palette = {
  primary: '#174B9B',
  primaryStrong: '#073F82',
  ink: '#172236',
  inkMuted: '#8D96A5',
  surface: '#FFFFFF',
  background: '#F4F7FB',
  border: '#DEE3EA',
  success: '#1E9E5A',
  warning: '#E59A1A',
  danger: '#D23B3B',
};

export const darkPalette: Palette = {
  primary: '#4C7FD0',
  primaryStrong: '#6B97DD',
  ink: '#E8EDF5',
  inkMuted: '#8D96A5',
  surface: '#111D30',
  background: '#0B1424',
  border: '#24324A',
  success: '#3CC27B',
  warning: '#F2B84B',
  danger: '#F06464',
};
