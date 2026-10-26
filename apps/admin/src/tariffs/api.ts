import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';

export type ModelTariff = components['schemas']['ModelTariffOut'];
export type TariffVersion = components['schemas']['TariffVersionOut'];
export type TariffPrices = components['schemas']['TariffPricesOut'];

/** Every call rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface TariffsApi {
  /** Every car model with its current tariff version, or without a tariff yet. */
  list(): Promise<ModelTariff[]>;
  /** Every version of a tariff, newest first. */
  versions(tariffId: string): Promise<TariffVersion[]>;
  create(modelId: string, prices: TariffPrices): Promise<ModelTariff>;
  /**
   * Saves a new version that applies from now on. `basedOn` is the version the form was filled
   * from: the API refuses with `tariff-changed` if someone saved a newer one meanwhile.
   */
  update(tariffId: string, basedOn: number, prices: TariffPrices): Promise<ModelTariff>;
}

export function createTariffsApi(client: AdminApiClient): TariffsApi {
  return {
    async list() {
      return unwrap(await client.GET('/admin/tariffs')).items;
    },
    async versions(tariffId) {
      return unwrap(
        await client.GET('/admin/tariffs/{tariff_id}/versions', {
          params: { path: { tariff_id: tariffId } },
        }),
      ).items;
    },
    async create(modelId, prices) {
      return unwrap(
        await client.POST('/admin/tariffs', { body: { model_id: modelId, ...prices } }),
      );
    },
    async update(tariffId, basedOn, prices) {
      return unwrap(
        await client.PUT('/admin/tariffs/{tariff_id}', {
          params: { path: { tariff_id: tariffId } },
          body: { based_on_version: basedOn, ...prices },
        }),
      );
    },
  };
}
