import type { Palette } from './colors';
import { fontFamily, fontSize } from './typography';
import { minTapTarget, radius, spacing } from './layout';

const px = (value: number): string => `${String(value)}px`;

const kebab = (name: string): string => name.replace(/[A-Z]/g, (char) => `-${char.toLowerCase()}`);

/**
 * CSS custom properties for a theme, e.g. `{ '--color-primary-strong': '#073F82', ... }`.
 * For the web admin: apply them to `:root` (light) and to the dark selector.
 */
export function cssVariables(palette: Palette): Record<string, string> {
  const variables: Record<string, string> = {};
  for (const [name, value] of Object.entries(palette)) {
    variables[`--color-${kebab(name)}`] = value;
  }
  return variables;
}

/** Theme-independent variables: spacing, radii, tap target, font sizes and families. */
export function cssStaticVariables(): Record<string, string> {
  const variables: Record<string, string> = {
    '--font-heading': `'${fontFamily.heading}', sans-serif`,
    '--font-body': `'${fontFamily.body}', sans-serif`,
    '--tap-target': px(minTapTarget),
  };
  for (const [name, value] of Object.entries(spacing)) variables[`--space-${name}`] = px(value);
  for (const [name, value] of Object.entries(radius)) variables[`--radius-${name}`] = px(value);
  for (const [name, value] of Object.entries(fontSize)) {
    variables[`--font-size-${name}`] = px(value);
  }
  return variables;
}
