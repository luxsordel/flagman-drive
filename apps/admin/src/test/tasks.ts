import { vi } from 'vitest';

import type { Task, TaskDetails, TasksApi } from '../tasks/api';

export function testTask(overrides: Partial<Task> = {}): Task {
  return {
    id: '0199d000-0000-7000-8000-000000000001',
    type: 'washing',
    status: 'new',
    description: null,
    assignee_id: null,
    created_by: null,
    comment: null,
    created_at: '2026-10-29T10:00:00Z',
    started_at: null,
    finished_at: null,
    car: {
      id: '0199c000-0000-7000-8000-000000000001',
      plate: '1234 AB-7',
      brand: 'BYD',
      model: 'Dolphin',
      status: 'available',
      lat: 53.9,
      lon: 27.56,
      battery_pct: 55,
      recorded_at: '2026-10-29T10:00:00Z',
    },
    ...overrides,
  };
}

/** The tasks API answering with `tasks`; actions return the first one as it is. */
export function fakeTasksApi(...tasks: Task[]) {
  const first = (): Task => tasks[0] ?? testTask();
  return {
    list: vi.fn<TasksApi['list']>(() => Promise.resolve({ items: tasks, total: tasks.length })),
    get: vi.fn<TasksApi['get']>((): Promise<TaskDetails> =>
      Promise.resolve({ ...first(), photos: [] }),
    ),
    technicians: vi.fn<TasksApi['technicians']>(() =>
      Promise.resolve([{ id: '0199a7c2-5d1e-7b3a-9f00-000000000002', email: 'tech@example.com' }]),
    ),
    create: vi.fn<TasksApi['create']>(() => Promise.resolve(first())),
    assign: vi.fn<TasksApi['assign']>(() => Promise.resolve(first())),
    start: vi.fn<TasksApi['start']>(() => Promise.resolve(first())),
    complete: vi.fn<TasksApi['complete']>(() => Promise.resolve(first())),
    cancel: vi.fn<TasksApi['cancel']>(() => Promise.resolve(first())),
    command: vi.fn<TasksApi['command']>(() => Promise.resolve(first())),
    addPhoto: vi.fn<TasksApi['addPhoto']>(() => Promise.resolve([])),
    photoUrl: vi.fn<TasksApi['photoUrl']>(() => Promise.resolve('https://storage.test/photo')),
  } satisfies TasksApi;
}
