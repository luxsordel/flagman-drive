import { readFileSync } from 'node:fs';

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import { routeUrl, commandsFor, type Task } from '../tasks/api';
import { taskError } from '../tasks/problems';
import { testStaff } from '../test/auth';
import { fakeFleetApi } from '../test/fleet';
import { renderAt } from '../test/render';
import { fakeTasksApi, testTask } from '../test/tasks';

// jsdom has no WebGL; the map has its own thin component.
vi.mock('../fleet/FleetMap', () => ({
  FleetMap: ({ label }: { label: string }) => <div role="img" aria-label={label} />,
}));

const technician = testStaff('technician');

function openTask(name: RegExp) {
  fireEvent.click(screen.getByRole('button', { name }));
}

const inProgress = (overrides: Partial<Task> = {}): Task =>
  testTask({ status: 'in_progress', assignee_id: technician.id, ...overrides });

function stubGeolocation(geolocation: unknown) {
  Object.defineProperty(navigator, 'geolocation', { configurable: true, value: geolocation });
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'geolocation');
  vi.restoreAllMocks();
});

describe('tasks of a technician', () => {
  it('shows own open tasks first and the free ones on the other tab', async () => {
    const tasks = fakeTasksApi(testTask());
    renderAt('/tasks', technician, { tasks });

    expect(await screen.findByRole('button', { name: /1234 AB-7/ })).toBeDefined();
    expect(tasks.list).toHaveBeenCalledWith(
      expect.objectContaining({
        assignee_id: technician.id,
        status: ['new', 'assigned', 'in_progress'],
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: ru.tasks.tabs.free }));

    await waitFor(() => {
      expect(tasks.list).toHaveBeenCalledWith(expect.objectContaining({ free: true }));
    });
  });

  it('has neither the creation form nor assigning or cancelling', async () => {
    renderAt('/tasks', technician, { tasks: fakeTasksApi(testTask()) });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);

    expect(screen.queryByRole('heading', { name: ru.tasks.create.title })).toBeNull();
    expect(screen.queryByRole('button', { name: ru.tasks.cancel.submit })).toBeNull();
    expect(screen.queryByRole('button', { name: ru.tasks.assign.submit })).toBeNull();
  });

  it('takes a free task', async () => {
    const task = testTask();
    const tasks = fakeTasksApi(task);
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    fireEvent.click(await screen.findByRole('button', { name: ru.tasks.start }));

    await waitFor(() => {
      expect(tasks.start).toHaveBeenCalledWith(task.id);
    });
  });

  it('tells why a reserved car cannot be taken', async () => {
    const tasks = fakeTasksApi(testTask());
    tasks.start.mockRejectedValue(new ApiError(409, 'maintenance-car-busy'));
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    fireEvent.click(await screen.findByRole('button', { name: ru.tasks.start }));

    expect(await screen.findByText(ru.tasks.errors.carBusy)).toBeDefined();
  });

  it('opens the car from where the technician stands', async () => {
    stubGeolocation({
      getCurrentPosition: (ok: (position: unknown) => void) => {
        ok({ coords: { latitude: 53.9, longitude: 27.56 } });
      },
    });
    const task = inProgress();
    const tasks = fakeTasksApi(task);
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    fireEvent.click(await screen.findByRole('button', { name: ru.fleet.command.open }));

    await waitFor(() => {
      expect(tasks.command).toHaveBeenCalledWith(task.id, 'open', { lat: 53.9, lon: 27.56 });
    });
  });

  it('asks for the location when the browser gives none', async () => {
    stubGeolocation({
      getCurrentPosition: (_: unknown, failed: (error: { message: string }) => void) => {
        failed({ message: 'denied' });
      },
    });
    const tasks = fakeTasksApi(inProgress());
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    fireEvent.click(await screen.findByRole('button', { name: ru.fleet.command.open }));

    expect(await screen.findByText(ru.tasks.errors.noLocation)).toBeDefined();
    expect(tasks.command).not.toHaveBeenCalled();
  });

  it('offers charging commands on a charging task only', async () => {
    renderAt('/tasks', technician, { tasks: fakeTasksApi(inProgress({ type: 'charging' })) });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);

    expect(
      await screen.findByRole('button', { name: ru.fleet.command.start_charging }),
    ).toBeDefined();
    expect(commandsFor(testTask({ type: 'washing' }))).toEqual(['open', 'close']);
  });

  it('finishes the task with a comment and not without one', async () => {
    const task = inProgress();
    const tasks = fakeTasksApi(task);
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    const done = await screen.findByRole('button', { name: ru.tasks.complete.submit });
    fireEvent.click(done);
    expect(tasks.complete).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(ru.tasks.complete.comment), {
      target: { value: 'Вымыта снаружи' },
    });
    fireEvent.click(done);

    await waitFor(() => {
      expect(tasks.complete).toHaveBeenCalledWith(task.id, 'Вымыта снаружи');
    });
  });

  it('uploads a photo to the task', async () => {
    const task = inProgress();
    const tasks = fakeTasksApi(task);
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    const photo = new File(['bytes'], 'car.jpg', { type: 'image/jpeg' });
    fireEvent.change(await screen.findByLabelText(ru.tasks.photo.add), {
      target: { files: [photo] },
    });

    await waitFor(() => {
      expect(tasks.addPhoto).toHaveBeenCalledWith(task.id, photo);
    });
  });

  it('links to a route to the car', () => {
    expect(routeUrl(testTask().car)).toContain('rtext=~53.9,27.56');
    expect(routeUrl({ ...testTask().car, lat: null, lon: null })).toBeNull();
  });
});

describe('tasks of an operator', () => {
  const operator = testStaff('operator');

  it('filters by status and type', async () => {
    const tasks = fakeTasksApi(testTask());
    renderAt('/tasks', operator, { tasks });

    const filters = within(await screen.findByRole('group', { name: ru.tasks.filters.label }));
    fireEvent.change(filters.getByLabelText(ru.tasks.filters.type), {
      target: { value: 'repair' },
    });
    fireEvent.change(filters.getByLabelText(ru.tasks.filters.status), { target: { value: 'all' } });

    await waitFor(() => {
      expect(tasks.list).toHaveBeenLastCalledWith({ limit: 100, type: ['repair'] });
    });
  });

  it('creates a task for a chosen car', async () => {
    const tasks = fakeTasksApi();
    const fleet = fakeFleetApi();
    fleet.listCars.mockResolvedValue([
      { id: 'car-1', plate: '7777 AB-7' } as Awaited<ReturnType<typeof fleet.listCars>>[number],
    ]);
    renderAt('/tasks', operator, { tasks, fleet });

    const form = await screen.findByRole('form', { name: ru.tasks.create.title });
    await within(form).findByRole('option', { name: '7777 AB-7' });
    fireEvent.change(within(form).getByLabelText(ru.tasks.create.car), {
      target: { value: 'car-1' },
    });
    fireEvent.change(within(form).getByLabelText(ru.tasks.create.type), {
      target: { value: 'repair' },
    });
    fireEvent.click(within(form).getByRole('button', { name: ru.tasks.create.submit }));

    await waitFor(() => {
      expect(tasks.create).toHaveBeenCalledWith({
        car_id: 'car-1',
        type: 'repair',
        description: null,
        assignee_id: null,
      });
    });
  });

  it('assigns a technician and cancels a task', async () => {
    const task = testTask();
    const tasks = fakeTasksApi(task);
    renderAt('/tasks', operator, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    await screen.findByRole('option', { name: 'tech@example.com' });
    const assignee = screen.getAllByLabelText(ru.tasks.assign.label)[0];
    if (!assignee) throw new Error('no assignee field');
    fireEvent.change(assignee, {
      target: { value: '0199a7c2-5d1e-7b3a-9f00-000000000002' },
    });
    fireEvent.click(screen.getByRole('button', { name: ru.tasks.assign.submit }));
    await waitFor(() => {
      expect(tasks.assign).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole('button', { name: ru.tasks.cancel.submit }));
    await waitFor(() => {
      expect(tasks.cancel).toHaveBeenCalledWith(task.id, null);
    });
  });

  it('shows a failed load with a way to retry', async () => {
    const tasks = fakeTasksApi();
    tasks.list.mockRejectedValueOnce(new Error('offline'));
    renderAt('/tasks', operator, { tasks });

    fireEvent.click(await screen.findByRole('button', { name: ru.tasks.retry }));

    await waitFor(() => {
      expect(tasks.list).toHaveBeenCalledTimes(2);
    });
  });
});

describe('errors of tasks', () => {
  it.each([
    ['too-far-from-car', 'tooFar'],
    ['maintenance-charge-too-low', 'chargeTooLow'],
    ['maintenance-task-photo-limit', 'photoLimit'],
    ['telematics-timeout', 'timeout'],
  ])('tells %s as %s', (problem, key) => {
    expect(taskError(new ApiError(409, problem))).toEqual({ key });
  });

  it('keeps the reason of a car that refused', () => {
    expect(taskError(new ApiError(409, 'car-command-rejected', undefined, 'ignition_on'))).toEqual({
      key: 'rejected',
      reason: 'ignition_on',
    });
  });

  it('ends the session on a 401', () => {
    expect(taskError(new ApiError(401))).toBe('signOut');
  });
});

describe('a phone-sized screen', () => {
  // jsdom applies no CSS, so what a phone gets is read from the rules themselves.
  const styles = readFileSync('src/styles.css', 'utf-8');
  const phoneRules = styles.slice(styles.indexOf('@media (max-width: 40rem)'));

  it('stacks the menu over the page and shrinks the map', () => {
    expect(phoneRules).toMatch(/\.layout \{\s+grid-template-columns: minmax\(0, 1fr\);/);
    expect(phoneRules).toMatch(/\.fleet-map \{\s+height: 40vh;/);
  });

  it('puts the list under the map on the same breakpoint or wider', () => {
    expect(styles).toMatch(
      /@media \(max-width: 60rem\) \{\s+\.fleet-layout \{\s+grid-template-columns: minmax\(0, 1fr\);/,
    );
  });
});

describe('details of a task', () => {
  it('shows an operator who has the task', async () => {
    const assignee = '0199a7c2-5d1e-7b3a-9f00-000000000002';
    renderAt('/tasks', testStaff('operator'), {
      tasks: fakeTasksApi(testTask({ status: 'in_progress', assignee_id: assignee })),
    });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);

    expect(await screen.findByText('tech@example.com')).toBeDefined();
  });

  it('offers an operator no way to take a task of a technician', async () => {
    renderAt('/tasks', testStaff('operator'), {
      tasks: fakeTasksApi(testTask({ status: 'assigned', assignee_id: 'someone' })),
    });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);

    expect(screen.queryByRole('button', { name: ru.tasks.start })).toBeNull();
  });

  it('loads the next page and keeps both', async () => {
    const tasks = fakeTasksApi(testTask());
    tasks.list.mockImplementation((query) =>
      Promise.resolve({
        items: [testTask({ id: `task-${String(query.offset ?? 0)}` })],
        total: 2,
      }),
    );
    renderAt('/tasks', testStaff('operator'), { tasks });

    fireEvent.click(await screen.findByRole('button', { name: ru.tasks.more }));

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /1234 AB-7/ })).toHaveLength(2);
    });
    expect(tasks.list).toHaveBeenCalledWith(expect.objectContaining({ offset: 100 }));
  });

  it('closes the opened tab and tells when a photo link fails', async () => {
    const tab = { close: vi.fn(), location: { href: '' }, opener: null };
    vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window);
    const task = testTask();
    const tasks = fakeTasksApi(task);
    tasks.get.mockResolvedValue({
      ...task,
      photos: [{ id: 'photo-1', uploaded_at: '2026-10-29T10:00:00Z' }],
    });
    tasks.photoUrl.mockRejectedValue(new ApiError(429, 'rate-limited', 7));
    renderAt('/tasks', testStaff('operator'), { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    fireEvent.click(await screen.findByRole('button', { name: 'Фото 1' }));

    expect(await screen.findByText(/Подождите 7/)).toBeDefined();
    expect(tab.close).toHaveBeenCalled();
  });

  it('unplugs the car before finishing a charging task, whatever the car answers', async () => {
    stubGeolocation({
      getCurrentPosition: (ok: (position: unknown) => void) => {
        ok({ coords: { latitude: 53.9, longitude: 27.56 } });
      },
    });
    const task = inProgress({ type: 'charging' });
    const tasks = fakeTasksApi(task);
    tasks.command.mockRejectedValue(new ApiError(503, 'telematics-unavailable'));
    renderAt('/tasks', technician, { tasks });

    await screen.findByRole('button', { name: /1234 AB-7/ });
    openTask(/1234 AB-7/);
    fireEvent.change(await screen.findByLabelText(ru.tasks.complete.comment), {
      target: { value: 'Заряжена' },
    });
    fireEvent.click(screen.getByRole('button', { name: ru.tasks.complete.submit }));

    await waitFor(() => {
      expect(tasks.complete).toHaveBeenCalledWith(task.id, 'Заряжена');
    });
    expect(tasks.command).toHaveBeenCalledWith(
      task.id,
      'stop_charging',
      expect.objectContaining({ lat: 53.9 }),
    );
  });
});
