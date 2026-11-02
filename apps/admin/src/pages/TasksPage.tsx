import { useCallback, useEffect, useMemo, useState, type SyntheticEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { useApis } from '../api/context';
import { useAuth, useStaff } from '../auth/context';
import type { FleetCar } from '../fleet/api';
import { FleetMap } from '../fleet/FleetMap';
import type { MapCar } from '../fleet/mapStyle';
import { useInterval, listPollMs } from '../support/useInterval';
import {
  canPlanTasks,
  commandsFor,
  openStatuses,
  routeUrl,
  taskStatuses,
  taskTypes,
  type Position,
  type Task,
  type TaskCommand,
  type TaskDetails,
  type TaskQuery,
  type TaskStatus,
  type TaskType,
  type Technician,
} from '../tasks/api';
import { taskError, type TaskError } from '../tasks/problems';
import { formatMinskTime } from '../time/minsk';
import type { Zone } from '../zones/api';

const commentMinLength = 3;
const commentMaxLength = 500;
const pageSize = 100;

type TechnicianTab = 'mine' | 'free';
/** `open` is every open status together, `all` is no filter by status. */
type StatusFilter = 'open' | 'all' | TaskStatus;

/** The car of a task on the map, under the task's id: the map selects tasks here. */
type TaskOnMap = MapCar;

const onMap = (task: Task): TaskOnMap => ({
  id: task.id,
  status: task.car.status,
  state:
    task.car.lat !== null && task.car.lon !== null
      ? { lat: task.car.lat, lon: task.car.lon }
      : null,
});

/** The fleet's map draws stale cars faded; a task's list refreshes on its own instead. */
const neverStale = (): boolean => false;
const doNothing = (): void => undefined;

function currentPosition(): Promise<Position> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('no geolocation'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        resolve({ lat: coords.latitude, lon: coords.longitude });
      },
      (error) => {
        reject(new Error(error.message));
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  });
}

function ErrorNote({ error, command }: { error: TaskError | null; command?: TaskCommand | null }) {
  const { t } = useTranslation();
  if (!error) return null;
  const commandName = command ? t(`fleet.command.${command}`) : '';
  const reason =
    error.reason === 'ignition_on'
      ? t('fleet.reasons.ignition_on')
      : t('fleet.reasons.unknown', { code: error.reason });
  return (
    <p role="alert" className="list-error">
      {t(`tasks.errors.${error.key}`, {
        command: commandName,
        reason,
        seconds: error.retryAfterSeconds ?? 1,
      })}
    </p>
  );
}

/**
 * Technician tasks (docs/adr/0025). Operators and admins plan them over the whole list; a
 * technician, usually on a phone, sees their own and the free ones and works on them.
 */
export function TasksPage() {
  const { t } = useTranslation();
  const { tasks: tasksApi, zones: zonesApi } = useApis();
  const staff = useStaff();
  const { signOut } = useAuth();
  const planner = canPlanTasks(staff.role);
  const [tab, setTab] = useState<TechnicianTab>('mine');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open');
  const [typeFilter, setTypeFilter] = useState<TaskType | ''>('');
  const [tasks, setTasks] = useState<Task[]>([]);
  const [phase, setPhase] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [revision, setRevision] = useState(0);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);

  const query = useMemo((): TaskQuery => {
    if (!planner) {
      return tab === 'free'
        ? { free: true, limit: pageSize }
        : { assignee_id: staff.id, status: [...openStatuses], limit: pageSize };
    }
    return {
      limit: pageSize,
      ...(typeFilter ? { type: [typeFilter] } : {}),
      ...(statusFilter === 'all'
        ? {}
        : { status: statusFilter === 'open' ? [...openStatuses] : [statusFilter] }),
    };
  }, [planner, tab, staff.id, statusFilter, typeFilter]);

  const refresh = useCallback(() => {
    setRevision((value) => value + 1);
  }, []);
  useInterval(refresh, listPollMs);

  useEffect(() => {
    let active = true;
    // Every shown page again: the list refreshes on its own, and tasks move between pages.
    const loads = Array.from({ length: pages }, (_, page) =>
      tasksApi.list({ ...query, ...(page > 0 ? { offset: page * pageSize } : {}) }),
    );
    Promise.all(loads).then(
      (loaded) => {
        if (!active) return;
        setTasks(loaded.flatMap(({ items }) => items));
        setTotal(loaded[0]?.total ?? 0);
        setPhase('ready');
      },
      (error: unknown) => {
        if (!active) return;
        if (taskError(error) === 'signOut') void signOut();
        setPhase('failed');
      },
    );
    return () => {
      active = false;
    };
  }, [tasksApi, query, pages, revision, signOut]);

  useEffect(() => {
    let active = true;
    // The tasks matter more than the zones under them: without zones the map still works.
    zonesApi.list().then(
      (loaded) => {
        if (active) setZones(loaded);
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [zonesApi]);

  const selected = tasks.find((task) => task.id === selectedId) ?? null;
  const cars = useMemo(() => tasks.map(onMap), [tasks]);

  return (
    <>
      <h1>{t('nav.tasks')}</h1>
      {planner ? (
        <div className="filters" role="group" aria-label={t('tasks.filters.label')}>
          <label className="stacked-label">
            {t('tasks.filters.status')}
            <select
              className="field"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value as StatusFilter);
                setPages(1);
              }}
            >
              <option value="open">{t('tasks.filters.anyOpen')}</option>
              <option value="all">{t('tasks.filters.anyStatus')}</option>
              {taskStatuses.map((status) => (
                <option key={status} value={status}>
                  {t(`tasks.status.${status}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="stacked-label">
            {t('tasks.filters.type')}
            <select
              className="field"
              value={typeFilter}
              onChange={(event) => {
                setTypeFilter(event.target.value as TaskType | '');
                setPages(1);
              }}
            >
              <option value="">{t('tasks.filters.anyType')}</option>
              {taskTypes.map((type) => (
                <option key={type} value={type}>
                  {t(`tasks.type.${type}`)}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : (
        <div className="tasks-tabs" role="group" aria-label={t('tasks.tabs.label')}>
          {(['mine', 'free'] as const).map((key) => (
            <button
              key={key}
              type="button"
              className={tab === key ? 'button button-primary' : 'button'}
              aria-pressed={tab === key}
              onClick={() => {
                setTab(key);
                setPages(1);
                setSelectedId(null);
              }}
            >
              {t(`tasks.tabs.${key}`)}
            </button>
          ))}
        </div>
      )}
      <div className="fleet-layout">
        <FleetMap
          cars={cars}
          zones={zones}
          selectedId={selectedId}
          isStale={neverStale}
          onSelect={setSelectedId}
          label={t('tasks.map')}
          unsupportedText={t('tasks.mapUnsupported')}
        />
        <aside className="fleet-panel">
          {phase === 'loading' && (
            <p role="status" className="muted">
              {t('tasks.loading')}
            </p>
          )}
          {phase === 'failed' && (
            <div role="alert">
              <p className="list-error">{t('tasks.loadFailed')}</p>
              <button type="button" className="button" onClick={refresh}>
                {t('tasks.retry')}
              </button>
            </div>
          )}
          {phase === 'ready' && tasks.length === 0 && <p className="muted">{t('tasks.empty')}</p>}
          {tasks.length > 0 && (
            <ul className="fleet-list" aria-label={t('tasks.list')}>
              {tasks.map((task) => (
                <li key={task.id}>
                  <button
                    type="button"
                    className="fleet-item"
                    aria-pressed={task.id === selectedId}
                    onClick={() => {
                      setSelectedId(task.id);
                    }}
                  >
                    <span>
                      {t('tasks.item', {
                        type: t(`tasks.type.${task.type}`),
                        plate: task.car.plate,
                      })}
                    </span>
                    <span className="muted">
                      {t(`tasks.status.${task.status}`)}
                      {' · '}
                      {task.car.battery_pct === null
                        ? t('tasks.noTelemetry')
                        : t('tasks.battery', { pct: task.car.battery_pct })}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {tasks.length < total && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setPages((value) => value + 1);
              }}
            >
              {t('tasks.more')}
            </button>
          )}
          {selected && (
            // Keyed by task: a comment typed for one task never goes to another.
            <TaskCard
              key={selected.id}
              task={selected}
              onChanged={refresh}
              onTaken={() => {
                setTab('mine');
                setPages(1);
              }}
            />
          )}
          {planner && <NewTaskForm onCreated={refresh} />}
        </aside>
      </div>
    </>
  );
}

function useAction(onChanged: () => void) {
  const { signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<TaskError | null>(null);
  const run = useCallback(
    async (action: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        onChanged();
        return true;
      } catch (failure) {
        const problem = taskError(failure);
        if (problem === 'signOut') void signOut();
        else {
          setError(problem);
          // The refusal may mean the task has moved on: show what it is now.
          onChanged();
        }
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onChanged, signOut],
  );
  return { busy, error, setError, run };
}

function TaskCard({
  task,
  onChanged,
  onTaken,
}: {
  task: Task;
  onChanged: () => void;
  // A taken task leaves the free list, and the technician's work on it is on the card.
  onTaken: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { tasks: tasksApi } = useApis();
  const staff = useStaff();
  const planner = canPlanTasks(staff.role);
  const technicians = useTechnicians(planner);
  const [details, setDetails] = useState<TaskDetails | null>(null);
  const reload = useCallback(() => {
    tasksApi.get(task.id).then(setDetails, () => undefined);
    onChanged();
  }, [tasksApi, task.id, onChanged]);
  const { busy, error, run } = useAction(reload);
  const mine = task.assignee_id === staff.id;
  const open = openStatuses.includes(task.status);
  const canStart =
    (task.status === 'new' || task.status === 'assigned') && (task.assignee_id === null || mine);
  const route = routeUrl(task.car);

  useEffect(() => {
    let active = true;
    tasksApi.get(task.id).then(
      (loaded) => {
        if (active) setDetails(loaded);
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [tasksApi, task.id, task.status]);

  return (
    <section className="card fleet-card" aria-labelledby="task-card-title">
      <h2 id="task-card-title">
        {t('tasks.item', { type: t(`tasks.type.${task.type}`), plate: task.car.plate })}
      </h2>
      <dl className="details">
        <dt>{t('tasks.card.car')}</dt>
        <dd>{`${task.car.brand} ${task.car.model}`}</dd>
        <dt>{t('tasks.card.status')}</dt>
        <dd>{t(`tasks.status.${task.status}`)}</dd>
        {planner && (
          <>
            <dt>{t('tasks.card.assignee')}</dt>
            <dd>
              {task.assignee_id === null
                ? t('tasks.card.nobody')
                : (technicians.find(({ id }) => id === task.assignee_id)?.email ??
                  t('tasks.card.otherStaff'))}
            </dd>
          </>
        )}
        <dt>{t('tasks.card.createdAt')}</dt>
        <dd>
          <time dateTime={task.created_at}>{formatMinskTime(task.created_at, i18n.language)}</time>
          {task.created_by === null && ` · ${t('tasks.card.automatic')}`}
        </dd>
        {task.car.battery_pct !== null && (
          <>
            <dt>{t('fleet.card.battery')}</dt>
            <dd>{t('tasks.battery', { pct: task.car.battery_pct })}</dd>
          </>
        )}
        {task.description && (
          <>
            <dt>{t('tasks.card.description')}</dt>
            <dd>{task.description}</dd>
          </>
        )}
        {task.comment && (
          <>
            <dt>{t('tasks.card.comment')}</dt>
            <dd>{task.comment}</dd>
          </>
        )}
      </dl>
      {route && open && (
        <a href={route} target="_blank" rel="noreferrer">
          {t('tasks.card.route')}
        </a>
      )}
      {details && details.photos.length > 0 && (
        <TaskPhotos taskId={task.id} photoIds={details.photos.map((photo) => photo.id)} />
      )}
      {canStart && (
        <button
          type="button"
          className="button button-primary"
          disabled={busy}
          onClick={() => {
            void run(() => tasksApi.start(task.id)).then((taken) => {
              if (taken) onTaken();
            });
          }}
        >
          {t('tasks.start')}
        </button>
      )}
      <ErrorNote error={error} />
      {task.status === 'in_progress' && mine && <TaskWorkPanel task={task} onChanged={reload} />}
      {planner && (task.status === 'new' || task.status === 'assigned') && (
        <AssignForm task={task} onChanged={reload} />
      )}
      {planner && open && <CancelForm task={task} onChanged={reload} />}
    </section>
  );
}

function TaskPhotos({ taskId, photoIds }: { taskId: string; photoIds: string[] }) {
  const { t } = useTranslation();
  const { tasks: tasksApi } = useApis();
  const { error, run } = useAction(doNothing);

  const open = async (photoId: string) => {
    // Opened on the click itself: a phone's browser blocks a window opened after a request.
    const tab = window.open('about:blank', '_blank');
    // Signed for minutes: asked for on the click, never kept in the page.
    const opened = await run(async () => {
      const url = await tasksApi.photoUrl(taskId, photoId);
      if (tab) {
        tab.opener = null;
        tab.location.href = url;
      }
    });
    if (!opened) tab?.close();
  };

  return (
    <section aria-label={t('tasks.card.photos')}>
      <p className="muted">{t('tasks.card.photos')}</p>
      <ul className="tasks-photos">
        {photoIds.map((photoId, index) => (
          <li key={photoId}>
            <button
              type="button"
              className="link-button"
              onClick={() => {
                void open(photoId);
              }}
            >
              {t('tasks.card.openPhoto', { number: index + 1 })}
            </button>
          </li>
        ))}
      </ul>
      <ErrorNote error={error} />
    </section>
  );
}

function TaskWorkPanel({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const { t } = useTranslation();
  const { tasks: tasksApi } = useApis();
  const { busy, error, setError, run } = useAction(onChanged);
  const [comment, setComment] = useState('');
  const [tooShort, setTooShort] = useState(false);
  const [lastCommand, setLastCommand] = useState<TaskCommand | null>(null);
  const [sent, setSent] = useState<TaskCommand | null>(null);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);

  const send = async (command: TaskCommand) => {
    setLastCommand(command);
    setSent(null);
    setSending(true);
    let at: Position;
    try {
      at = await currentPosition();
    } catch {
      setError({ key: 'noLocation' });
      setSending(false);
      return;
    }
    if (await run(() => tasksApi.command(task.id, command, at))) setSent(command);
    setSending(false);
  };

  const complete = (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault();
    if (comment.trim().length < commentMinLength) {
      setTooShort(true);
      return;
    }
    setTooShort(false);
    void run(() => tasksApi.complete(task.id, comment.trim()));
  };

  return (
    <>
      <h3>{t('tasks.commands')}</h3>
      <div className="fleet-commands">
        {commandsFor(task).map((command) => (
          <button
            key={command}
            type="button"
            className="button"
            disabled={busy || sending}
            onClick={() => {
              void send(command);
            }}
          >
            {t(`fleet.command.${command}`)}
          </button>
        ))}
      </div>
      {sending && (
        <p role="status" className="muted">
          {t('tasks.sending')}
        </p>
      )}
      {sent && !sending && (
        <p role="status" className="fleet-done">
          {t('tasks.sent', { command: t(`fleet.command.${sent}`) })}
        </p>
      )}
      <ErrorNote error={error} command={lastCommand} />
      <label className="stacked-label">
        {uploading ? t('tasks.photo.uploading') : t('tasks.photo.add')}
        <input
          className="field"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          disabled={uploading}
          onChange={(event) => {
            const input = event.target;
            const photo = input.files?.[0];
            if (!photo) return;
            setUploading(true);
            void run(() => tasksApi.addPhoto(task.id, photo)).finally(() => {
              setUploading(false);
              input.value = '';
            });
          }}
        />
      </label>
      <form className="tasks-form" onSubmit={complete}>
        <label className="stacked-label">
          {t('tasks.complete.comment')}
          <textarea
            className="textarea"
            value={comment}
            maxLength={commentMaxLength}
            onChange={(event) => {
              setComment(event.target.value);
            }}
          />
        </label>
        {tooShort && (
          <p role="alert" className="list-error">
            {t('tasks.complete.commentTooShort', { min: commentMinLength })}
          </p>
        )}
        <button type="submit" className="button button-primary" disabled={busy || sending}>
          {t('tasks.complete.submit')}
        </button>
      </form>
    </>
  );
}

function useTechnicians(enabled = true): Technician[] {
  const { tasks: tasksApi } = useApis();
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    tasksApi.technicians().then(
      (loaded) => {
        if (active) setTechnicians(loaded);
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [tasksApi, enabled]);
  return technicians;
}

function AssignForm({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const { t } = useTranslation();
  const { tasks: tasksApi } = useApis();
  const technicians = useTechnicians();
  const { busy, error, run } = useAction(onChanged);
  const [assignee, setAssignee] = useState(task.assignee_id ?? '');

  return (
    <form
      className="tasks-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (assignee) void run(() => tasksApi.assign(task.id, assignee));
      }}
    >
      <label className="stacked-label">
        {t('tasks.assign.label')}
        <select
          className="field"
          value={assignee}
          onChange={(event) => {
            setAssignee(event.target.value);
          }}
        >
          <option value="">{t('tasks.assign.pick')}</option>
          {technicians.map((technician) => (
            <option key={technician.id} value={technician.id}>
              {technician.email}
            </option>
          ))}
        </select>
      </label>
      <ErrorNote error={error} />
      <button type="submit" className="button" disabled={busy || !assignee}>
        {t('tasks.assign.submit')}
      </button>
    </form>
  );
}

function CancelForm({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const { t } = useTranslation();
  const { tasks: tasksApi } = useApis();
  const { busy, error, run } = useAction(onChanged);
  const [comment, setComment] = useState('');

  return (
    <form
      className="tasks-form"
      onSubmit={(event) => {
        event.preventDefault();
        void run(() => tasksApi.cancel(task.id, comment.trim() || null));
      }}
    >
      <label className="stacked-label">
        {t('tasks.cancel.comment')}
        <input
          className="field"
          value={comment}
          maxLength={commentMaxLength}
          onChange={(event) => {
            setComment(event.target.value);
          }}
        />
      </label>
      <ErrorNote error={error} />
      <button type="submit" className="button button-danger" disabled={busy}>
        {t('tasks.cancel.submit')}
      </button>
    </form>
  );
}

function NewTaskForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useTranslation();
  const { fleet, tasks: tasksApi } = useApis();
  const technicians = useTechnicians();
  const { busy, error, run } = useAction(onCreated);
  const [cars, setCars] = useState<FleetCar[]>([]);
  const [carId, setCarId] = useState('');
  const [type, setType] = useState<TaskType>('washing');
  const [description, setDescription] = useState('');
  const [assignee, setAssignee] = useState('');

  useEffect(() => {
    let active = true;
    fleet.listCars().then(
      (loaded) => {
        if (active) setCars(loaded);
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, [fleet]);

  const submit = async (event: SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    event.preventDefault();
    const created = await run(() =>
      tasksApi.create({
        car_id: carId,
        type,
        description: description.trim() || null,
        assignee_id: assignee || null,
      }),
    );
    if (created) {
      setCarId('');
      setDescription('');
      setAssignee('');
    }
  };

  return (
    <form
      className="card tasks-form"
      aria-labelledby="task-new-title"
      onSubmit={(event) => {
        void submit(event);
      }}
    >
      <h2 id="task-new-title">{t('tasks.create.title')}</h2>
      <label className="stacked-label">
        {t('tasks.create.car')}
        <select
          className="field"
          value={carId}
          onChange={(event) => {
            setCarId(event.target.value);
          }}
        >
          <option value="">{t('tasks.create.pickCar')}</option>
          {cars.map((car) => (
            <option key={car.id} value={car.id}>
              {car.plate}
            </option>
          ))}
        </select>
      </label>
      <label className="stacked-label">
        {t('tasks.create.type')}
        <select
          className="field"
          value={type}
          onChange={(event) => {
            setType(event.target.value as TaskType);
          }}
        >
          {taskTypes.map((value) => (
            <option key={value} value={value}>
              {t(`tasks.type.${value}`)}
            </option>
          ))}
        </select>
      </label>
      <label className="stacked-label">
        {t('tasks.create.description')}
        <textarea
          className="textarea"
          value={description}
          maxLength={commentMaxLength}
          onChange={(event) => {
            setDescription(event.target.value);
          }}
        />
      </label>
      <label className="stacked-label">
        {t('tasks.create.assignee')}
        <select
          className="field"
          value={assignee}
          onChange={(event) => {
            setAssignee(event.target.value);
          }}
        >
          <option value="">{t('tasks.create.nobody')}</option>
          {technicians.map((technician) => (
            <option key={technician.id} value={technician.id}>
              {technician.email}
            </option>
          ))}
        </select>
      </label>
      <ErrorNote error={error} />
      <button type="submit" className="button button-primary" disabled={busy || !carId}>
        {t('tasks.create.submit')}
      </button>
    </form>
  );
}
