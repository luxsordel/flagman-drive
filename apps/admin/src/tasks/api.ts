import type { components, paths } from '@flagman/api-client';

import { unwrap, type AdminApiClient } from '../api/client';
import type { StaffRole } from '../auth/staff';

export type Task = components['schemas']['TaskOut'];
export type TaskDetails = components['schemas']['TaskDetailsOut'];
export type TaskCar = components['schemas']['TaskCarOut'];
export type TaskStatus = components['schemas']['TaskStatus'];
export type TaskType = components['schemas']['TaskType'];
export type TaskCommand = components['schemas']['TaskCommandIn']['command'];
export type TaskInput = components['schemas']['TaskIn'];
export type TaskPhoto = components['schemas']['TaskPhotoOut'];
export type Technician = components['schemas']['TechnicianOut'];
export type TaskQuery = NonNullable<
  paths['/admin/maintenance/tasks']['get']['parameters']['query']
>;

export const taskTypes: readonly TaskType[] = [
  'charging',
  'washing',
  'inspection',
  'repair',
  'other',
];

/** In the order of the status machine in maintenance/domain.py. */
export const taskStatuses: readonly TaskStatus[] = [
  'new',
  'assigned',
  'in_progress',
  'done',
  'cancelled',
];

export const openStatuses: readonly TaskStatus[] = ['new', 'assigned', 'in_progress'];

/** The same roles as TASK_PLANNERS in maintenance/api.py: they create, assign and cancel. */
const plannerRoles: readonly StaffRole[] = ['operator', 'admin'];

export const canPlanTasks = (role: StaffRole): boolean => plannerRoles.includes(role);

/** Charging commands are for charging tasks only, as the API checks. */
export function commandsFor(task: Task): TaskCommand[] {
  return task.type === 'charging'
    ? ['open', 'close', 'start_charging', 'stop_charging']
    : ['open', 'close'];
}

/** Where the technician is: the API checks the distance to the car with it, never stores it. */
export interface Position {
  lat: number;
  lon: number;
}

/** Rejects with ApiError when the API refuses, with NetworkError when it is unreachable. */
export interface TasksApi {
  list(query: TaskQuery): Promise<{ items: Task[]; total: number }>;
  get(taskId: string): Promise<TaskDetails>;
  technicians(): Promise<Technician[]>;
  create(input: TaskInput): Promise<Task>;
  assign(taskId: string, assigneeId: string): Promise<Task>;
  start(taskId: string): Promise<Task>;
  complete(taskId: string, comment: string): Promise<Task>;
  cancel(taskId: string, comment: string | null): Promise<Task>;
  /** Waits for the car, like the fleet's commands. */
  command(taskId: string, command: TaskCommand, at: Position): Promise<Task>;
  addPhoto(taskId: string, photo: Blob): Promise<TaskPhoto[]>;
  photoUrl(taskId: string, photoId: string): Promise<string>;
}

export function createTasksApi(client: AdminApiClient): TasksApi {
  const path = (taskId: string) => ({ params: { path: { task_id: taskId } } });
  return {
    async list(query) {
      return unwrap(await client.GET('/admin/maintenance/tasks', { params: { query } }));
    },
    async get(taskId) {
      return unwrap(await client.GET('/admin/maintenance/tasks/{task_id}', path(taskId)));
    },
    async technicians() {
      return unwrap(await client.GET('/admin/maintenance/technicians')).items;
    },
    async create(input) {
      return unwrap(await client.POST('/admin/maintenance/tasks', { body: input }));
    },
    async assign(taskId, assigneeId) {
      return unwrap(
        await client.POST('/admin/maintenance/tasks/{task_id}/assign', {
          ...path(taskId),
          body: { assignee_id: assigneeId },
        }),
      );
    },
    async start(taskId) {
      return unwrap(await client.POST('/admin/maintenance/tasks/{task_id}/start', path(taskId)));
    },
    async complete(taskId, comment) {
      return unwrap(
        await client.POST('/admin/maintenance/tasks/{task_id}/complete', {
          ...path(taskId),
          body: { comment },
        }),
      );
    },
    async cancel(taskId, comment) {
      return unwrap(
        await client.POST('/admin/maintenance/tasks/{task_id}/cancel', {
          ...path(taskId),
          body: { comment },
        }),
      );
    },
    async command(taskId, command, at) {
      const result = unwrap(
        await client.POST('/admin/maintenance/tasks/{task_id}/commands', {
          ...path(taskId),
          body: { command, lat: at.lat, lon: at.lon },
        }),
      );
      return result.task;
    },
    async addPhoto(taskId, photo) {
      const result = unwrap(
        await client.POST('/admin/maintenance/tasks/{task_id}/photos', {
          ...path(taskId),
          // The schema types the raw body as a string; the bytes go as they are.
          body: '',
          bodySerializer: () => photo,
          headers: { 'Content-Type': photo.type || 'image/jpeg' },
        }),
      );
      return result.photos;
    },
    async photoUrl(taskId, photoId) {
      const result = unwrap(
        await client.GET('/admin/maintenance/tasks/{task_id}/photos/{photo_id}/url', {
          params: { path: { task_id: taskId, photo_id: photoId } },
        }),
      );
      return result.url;
    },
  };
}

/** A route in the phone's map app from where the technician is to the car. */
export function routeUrl(car: TaskCar): string | null {
  if (car.lat === null || car.lon === null) return null;
  return `https://yandex.by/maps/?rtext=~${String(car.lat)},${String(car.lon)}&rtt=auto`;
}
