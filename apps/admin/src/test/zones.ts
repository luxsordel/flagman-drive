import { vi } from 'vitest';

import type { Zone, ZonesApi } from '../zones/api';

let counter = 0;

/** An allowed square in the centre of Minsk, unless `overrides` say otherwise. */
export function zone(overrides: Partial<Zone> = {}): Zone {
  counter += 1;
  return {
    id: `0199c000-2222-7000-8000-${String(counter).padStart(12, '0')}`,
    name: `Zone ${String(counter)}`,
    kind: 'allowed',
    polygon: {
      type: 'Polygon',
      coordinates: [
        [
          [27.55, 53.89],
          [27.57, 53.89],
          [27.57, 53.91],
          [27.55, 53.91],
          [27.55, 53.89],
        ],
      ],
    },
    created_at: '2026-10-25T09:00:00Z',
    updated_at: '2026-10-25T09:00:00Z',
    disabled_at: null,
    ...overrides,
  };
}

/** The zones API answering with `zones`; changes echo the zone back as the API would. */
export function fakeZonesApi(zones: Zone[] = []) {
  return {
    list: vi.fn<ZonesApi['list']>(() => Promise.resolve(zones)),
    create: vi.fn<ZonesApi['create']>((input) => Promise.resolve(zone({ ...input }))),
    update: vi.fn<ZonesApi['update']>((id, input) => Promise.resolve(zone({ ...input, id }))),
    disable: vi.fn<ZonesApi['disable']>((id) =>
      Promise.resolve(zone({ id, disabled_at: '2026-10-26T10:00:00Z' })),
    ),
  } satisfies ZonesApi;
}
