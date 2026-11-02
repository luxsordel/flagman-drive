import type { components } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';
import type { StaffRole } from '../auth/staff';

export type FleetCar = components['schemas']['StaffCarOut'];
export type FleetRules = components['schemas']['FleetRulesOut'];
export type CarStatus = components['schemas']['CarStatus'];
export type CarCommand = components['schemas']['CarCommand'];
export type CarCommandInput = components['schemas']['CarCommandIn'];
export type CarCommandResult = components['schemas']['CarCommandOut'];

/** In menu order: the statuses of the status machine in fleet/domain.py. */
export const carStatuses: readonly CarStatus[] = [
  'available',
  'reserved',
  'in_rental',
  'service',
  'offline',
];

export const carCommands: readonly CarCommand[] = ['open', 'close', 'lock_engine', 'unlock_engine'];

/**
 * The same roles as CAR_COMMANDERS in fleet/api.py; the API checks them on every command.
 * Technicians reach only the car of their task, from the task (docs/adr/0025).
 */
const commandRoles: readonly StaffRole[] = ['operator', 'admin'];

export const canSendCommands = (role: StaffRole): boolean => commandRoles.includes(role);

/** A client holds the car: the API wants `confirm_active_rental` for a command to it. */
export const needsRentalConfirmation = (status: CarStatus): boolean =>
  status === 'reserved' || status === 'in_rental';

/** Rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface FleetApi {
  listCars(): Promise<FleetCar[]>;
  /** Waits for the car: resolves with the car after the command. */
  sendCommand(carId: string, input: CarCommandInput): Promise<CarCommandResult>;
}

export function createFleetApi(client: AdminApiClient): FleetApi {
  return {
    async listCars() {
      return unwrap(await client.GET('/admin/fleet/cars')).items;
    },
    async sendCommand(carId, input) {
      return unwrap(
        await client.POST('/admin/fleet/cars/{car_id}/commands', {
          params: { path: { car_id: carId } },
          body: input,
        }),
      );
    },
  };
}
