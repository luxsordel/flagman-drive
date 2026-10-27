import { vi } from 'vitest';

import type { ModelTariff, TariffPrices, TariffsApi, TariffVersion } from '../tariffs/api';

let counter = 0;

export const demoPrices: TariffPrices = {
  driving_per_minute: 39,
  parked_per_minute: 12,
  waiting_per_minute: 12,
  free_waiting_minutes: 15,
  hold_amount: 3000,
};

export function tariffVersion(overrides: Partial<TariffVersion> = {}): TariffVersion {
  counter += 1;
  return {
    id: `0199c000-3333-7000-8000-${String(counter).padStart(12, '0')}`,
    number: 1,
    prices: demoPrices,
    valid_from: '2026-10-26T09:00:00Z',
    created_by: null,
    ...overrides,
  };
}

/** A car model with the demo tariff, or without a tariff when `current` is null. */
export function modelTariff(
  name: string,
  current: TariffVersion | null = tariffVersion(),
): ModelTariff {
  counter += 1;
  const suffix = String(counter).padStart(12, '0');
  return {
    model: { id: `0199c000-4444-7000-8000-${suffix}`, brand: 'BYD', name },
    tariff_id: current ? `0199c000-5555-7000-8000-${suffix}` : null,
    current,
  };
}

/** The tariffs API answering with `items`; saves echo a new version back as the API would. */
export function fakeTariffsApi(items: ModelTariff[] = []) {
  return {
    list: vi.fn<TariffsApi['list']>(() => Promise.resolve(items)),
    versions: vi.fn<TariffsApi['versions']>((tariffId) => {
      const item = items.find((candidate) => candidate.tariff_id === tariffId);
      return Promise.resolve(item?.current ? [item.current] : []);
    }),
    create: vi.fn<TariffsApi['create']>((modelId, prices) => {
      const item = items.find((candidate) => candidate.model.id === modelId);
      return Promise.resolve({
        model: item?.model ?? { id: modelId, brand: 'BYD', name: 'New' },
        tariff_id: '0199c000-6666-7000-8000-000000000001',
        current: tariffVersion({ prices }),
      });
    }),
    update: vi.fn<TariffsApi['update']>((tariffId, basedOn, prices) => {
      const item = items.find((candidate) => candidate.tariff_id === tariffId);
      return Promise.resolve({
        model: item?.model ?? { id: 'unknown', brand: 'BYD', name: 'Unknown' },
        tariff_id: tariffId,
        current: tariffVersion({ number: basedOn + 1, prices, valid_from: '2026-10-26T12:00:00Z' }),
      });
    }),
  } satisfies TariffsApi;
}
