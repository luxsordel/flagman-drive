import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';

type ZoneOut = components['schemas']['ZoneOut'];
/** As the client returns it: response bodies come with plain arrays, not `[lon, lat]` tuples. */
export type Zone = Omit<ZoneOut, 'polygon'> & {
  polygon: { type: 'Polygon'; coordinates: number[][][] };
};
export type ZoneKind = components['schemas']['ZoneKind'];
export type ZoneInput = components['schemas']['ZoneIn'];

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface ZonesApi {
  /** Active zones in the order they were drawn. */
  list(): Promise<Zone[]>;
  create(zone: ZoneInput): Promise<Zone>;
  /** Replaces the name, the kind and the outline at once. */
  update(id: string, zone: ZoneInput): Promise<Zone>;
  /** Zones are never deleted: a disabled one stays in the audit log and the history. */
  disable(id: string): Promise<Zone>;
}

export function createZonesApi(client: AdminApiClient): ZonesApi {
  return {
    async list() {
      return unwrap(await client.GET('/admin/zones')).items;
    },
    async create(zone) {
      return unwrap(await client.POST('/admin/zones', { body: zone }));
    },
    async update(id, zone) {
      return unwrap(
        await client.PUT('/admin/zones/{zone_id}', {
          params: { path: { zone_id: id } },
          body: zone,
        }),
      );
    },
    async disable(id) {
      return unwrap(
        await client.POST('/admin/zones/{zone_id}/disable', { params: { path: { zone_id: id } } }),
      );
    },
  };
}
