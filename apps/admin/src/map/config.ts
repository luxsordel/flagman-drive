/**
 * Map settings shared by every map of the panel: zones now, the fleet (#96) later.
 * The tile provider is one line of config (ADR-0003): set `VITE_MAP_STYLE_URL` to switch it.
 */

/** Free OpenStreetMap tiles without a key, good enough for the pilot (ADR-0003). */
const defaultStyleUrl = 'https://tiles.openfreemap.org/styles/liberty';

export function mapStyleUrl(): string {
  const configured = import.meta.env.VITE_MAP_STYLE_URL;
  return configured ? configured : defaultStyleUrl;
}

/** Where a map opens before it has anything to show: the centre of Minsk, `[lon, lat]`. */
export const minskCenter: [number, number] = [27.5615, 53.9023];
export const cityZoom = 11;
