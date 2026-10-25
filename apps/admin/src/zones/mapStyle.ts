import type { Palette } from '@flagman/design-tokens';
import type { ExpressionSpecification } from 'maplibre-gl';

import type { ZoneKind } from './api';

export const zonesSourceId = 'zones';
export const zonesFillLayerId = 'zones-fill';
export const zonesOutlineLayerId = 'zones-outline';

/** Where the end of a rental is allowed in green, cut-out forbidden pieces in red. */
export const zoneColor = (kind: ZoneKind, palette: Palette): string =>
  kind === 'allowed' ? palette.success : palette.danger;

/** Terra Draw takes only `#rrggbb` colors; the design tokens are all written that way. */
export function hexColor(color: string): `#${string}` {
  if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error(`not a hex color: ${color}`);
  return color as `#${string}`;
}

/** The color of each zone feature by its `kind`. */
export const zoneColorExpression = (palette: Palette): ExpressionSpecification => [
  'match',
  ['get', 'kind'],
  'forbidden',
  zoneColor('forbidden', palette),
  zoneColor('allowed', palette),
];

// The selected zone stands out by a denser fill and a thicker outline, not by another color:
// its color still tells the kind.
export const zoneFillOpacity: ExpressionSpecification = ['case', ['get', 'selected'], 0.4, 0.2];
export const zoneOutlineWidth: ExpressionSpecification = ['case', ['get', 'selected'], 3, 1.5];
