import { useCallback, useEffect, useReducer, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useApis } from '../api/context';
import { useAuth, useStaff } from '../auth/context';
import {
  canSendCommands,
  carCommands,
  carStatuses,
  needsRentalConfirmation,
  type CarCommand,
  type FleetCar,
  type FleetRules,
} from '../fleet/api';
import { FleetMap } from '../fleet/FleetMap';
import { commandError, type CommandError } from '../fleet/problems';
import {
  fleetReducer,
  initialFleetState,
  isLowBattery,
  isTelemetryStale,
  visibleCars,
  type FleetState,
} from '../fleet/state';
import type { CommandNotice } from '../fleet/stream';
import { formatMinskTime } from '../time/minsk';
import type { Zone } from '../zones/api';

/** Telemetry turns stale with time alone, so the marks are redrawn this often. */
const clockTickMs = 5_000;
const commentMinLength = 3;
const commentMaxLength = 300;

function useNow(): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, clockTickMs);
    return () => {
      clearInterval(timer);
    };
  }, []);
  return now;
}

/**
 * The live fleet for every staff role (docs/adr/0017): a map over the zones, a filtered list and
 * the card of the chosen car with its manual commands.
 */
export function FleetPage() {
  const { t } = useTranslation();
  const { fleetStream, zones: zonesApi } = useApis();
  const { signOut } = useAuth();
  const [state, dispatch] = useReducer(fleetReducer, initialFleetState);
  const [zones, setZones] = useState<Zone[]>([]);
  const now = useNow();

  useEffect(() => {
    const connection = fleetStream({
      onMessage: (message) => {
        dispatch({ type: 'message', message, receivedAt: Date.now() });
      },
      onStatus: (status) => {
        dispatch({ type: 'status', status });
      },
    });
    return () => {
      connection.close();
    };
  }, [fleetStream]);

  useEffect(() => {
    if (state.status === 'signedOut') void signOut();
  }, [state.status, signOut]);

  useEffect(() => {
    let active = true;
    // The cars matter more than the zones under them: without zones the map still works.
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

  const select = useCallback((id: string | null) => {
    dispatch({ type: 'select', id });
  }, []);
  const { rules, clockOffsetMs } = state;
  const isStale = useCallback(
    (car: FleetCar) => isTelemetryStale(car, rules, clockOffsetMs, now),
    [rules, clockOffsetMs, now],
  );

  const cars = visibleCars(state);
  const selected = state.selectedId ? (state.cars[state.selectedId] ?? null) : null;

  return (
    <>
      <h1>{t('nav.fleet')}</h1>
      <ConnectionNotice status={state.status} hasData={state.rules !== null} />
      <div className="fleet-layout">
        <FleetMap
          cars={cars}
          zones={zones}
          selectedId={state.selectedId}
          isStale={isStale}
          onSelect={select}
          label={t('fleet.map')}
          unsupportedText={t('fleet.mapUnsupported')}
        />
        <aside className="fleet-panel">
          <FleetFilters state={state} dispatch={dispatch} />
          {state.rules && (
            <CarList
              cars={cars}
              rules={state.rules}
              selectedId={state.selectedId}
              isStale={isStale}
              onSelect={select}
            />
          )}
          {selected && state.rules && (
            // Keyed by car: a confirmation started for one car never applies to another.
            <CarCard
              key={selected.id}
              car={selected}
              rules={state.rules}
              stale={isStale(selected)}
              lastCommand={state.lastCommands[selected.id] ?? null}
            />
          )}
        </aside>
      </div>
    </>
  );
}

function ConnectionNotice({ status, hasData }: { status: FleetState['status']; hasData: boolean }) {
  const { t } = useTranslation();
  if (status === 'live' || status === 'signedOut') return null;
  if (status === 'failed') {
    return (
      <p role="alert" className="card list-alert">
        {t('fleet.connection.failed')}
      </p>
    );
  }
  if (status === 'connecting' && !hasData) {
    return (
      <p role="status" className="muted">
        {t('fleet.connection.connecting')}
      </p>
    );
  }
  // Shown over the cars of the last snapshot: they stay, but may be out of date.
  return (
    <p role="status" className="card fleet-stale-notice">
      {t('fleet.connection.reconnecting')}
    </p>
  );
}

function FleetFilters({
  state,
  dispatch,
}: {
  state: FleetState;
  dispatch: (action: Parameters<typeof fleetReducer>[1]) => void;
}) {
  const { t } = useTranslation();
  return (
    <fieldset className="fleet-filters">
      <legend>{t('fleet.filters.title')}</legend>
      {carStatuses.map((status) => (
        <label key={status} className="inline-label">
          <input
            type="checkbox"
            checked={state.filter.statuses.includes(status)}
            onChange={() => {
              dispatch({ type: 'toggleStatus', status });
            }}
          />
          <span className={`fleet-swatch fleet-swatch-${status}`} aria-hidden="true" />
          {t(`fleet.status.${status}`)}
        </label>
      ))}
      <label className="inline-label">
        <input
          type="checkbox"
          checked={state.filter.lowBattery}
          onChange={(event) => {
            dispatch({ type: 'setLowBattery', lowBattery: event.target.checked });
          }}
        />
        {t('fleet.filters.lowBattery')}
      </label>
    </fieldset>
  );
}

function CarList({
  cars,
  rules,
  selectedId,
  isStale,
  onSelect,
}: {
  cars: readonly FleetCar[];
  rules: FleetRules;
  selectedId: string | null;
  isStale: (car: FleetCar) => boolean;
  onSelect: (id: string | null) => void;
}) {
  const { t } = useTranslation();
  if (cars.length === 0) return <p className="muted">{t('fleet.empty')}</p>;
  return (
    <ul className="fleet-list" aria-label={t('fleet.list')}>
      {cars.map((car) => (
        <li key={car.id}>
          <button
            type="button"
            className="fleet-item"
            aria-pressed={car.id === selectedId}
            onClick={() => {
              onSelect(car.id === selectedId ? null : car.id);
            }}
          >
            <span className={`fleet-swatch fleet-swatch-${car.status}`} aria-hidden="true" />
            <span>{car.plate}</span>
            <span className={isLowBattery(car, rules) ? 'fleet-low' : 'muted'}>
              {car.state ? t('fleet.battery', { pct: car.state.battery_pct }) : '—'}
            </span>
            <span className="muted">
              {t(`fleet.status.${car.status}`)}
              {isStale(car) && ` · ${t('fleet.stale')}`}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function CarCard({
  car,
  rules,
  stale,
  lastCommand,
}: {
  car: FleetCar;
  rules: FleetRules;
  stale: boolean;
  lastCommand: CommandNotice | null;
}) {
  const { t, i18n } = useTranslation();
  const { role } = useStaff();
  const { state } = car;

  return (
    <section className="card fleet-card" aria-labelledby="fleet-card-title">
      <h2 id="fleet-card-title">{car.plate}</h2>
      {stale && (
        <p role="note" className="fleet-stale">
          {t(state ? 'fleet.card.staleTelemetry' : 'fleet.card.noTelemetry')}
        </p>
      )}
      <dl className="details">
        <dt>{t('fleet.card.model')}</dt>
        <dd>{`${car.brand} ${car.model}`}</dd>
        <dt>{t('fleet.card.status')}</dt>
        <dd>{t(`fleet.status.${car.status}`)}</dd>
        {state && (
          <>
            <dt>{t('fleet.card.battery')}</dt>
            <dd className={isLowBattery(car, rules) ? 'fleet-low' : undefined}>
              {t('fleet.card.batteryValue', { pct: state.battery_pct, km: state.range_km })}
            </dd>
            <dt>{t('fleet.card.doors')}</dt>
            <dd>{t(state.doors_locked ? 'fleet.card.locked' : 'fleet.card.unlocked')}</dd>
            <dt>{t('fleet.card.ignition')}</dt>
            <dd>{t(state.ignition_on ? 'fleet.card.on' : 'fleet.card.off')}</dd>
            <dt>{t('fleet.card.engine')}</dt>
            <dd>
              {t(state.engine_locked ? 'fleet.card.engineLocked' : 'fleet.card.engineUnlocked')}
            </dd>
            <dt>{t('fleet.card.telemetryAt')}</dt>
            <dd>
              <time dateTime={state.recorded_at}>
                {formatMinskTime(state.recorded_at, i18n.language)}
              </time>
            </dd>
          </>
        )}
        <dt>{t('fleet.card.vin')}</dt>
        <dd>{car.vin}</dd>
      </dl>
      {lastCommand && (
        <p className="muted">
          {t('fleet.card.lastCommand', {
            command: t(`fleet.command.${lastCommand.command}`),
            outcome: t(`fleet.outcome.${lastCommand.outcome}`),
            at: formatMinskTime(lastCommand.at, i18n.language),
          })}
        </p>
      )}
      {canSendCommands(role) && <CommandPanel car={car} />}
    </section>
  );
}

type CommandPhase =
  | { phase: 'idle' }
  | { phase: 'confirming'; command: CarCommand }
  | { phase: 'sending'; command: CarCommand }
  | { phase: 'done'; command: CarCommand }
  | { phase: 'failed'; command: CarCommand; error: CommandError };

/** Refusal reasons of the emulator (car_emulator.domain.apply_command); others show their code. */
const knownReasons = ['ignition_on'] as const;
type KnownReason = (typeof knownReasons)[number];
const isKnownReason = (reason: string): reason is KnownReason =>
  (knownReasons as readonly string[]).includes(reason);

function CommandPanel({ car }: { car: FleetCar }) {
  const { t } = useTranslation();
  const reasonText = (reason: string | undefined): string => {
    if (reason === undefined) return '';
    return isKnownReason(reason)
      ? t(`fleet.reasons.${reason}`)
      : t('fleet.reasons.unknown', { code: reason });
  };
  const { fleet } = useApis();
  const { signOut } = useAuth();
  const [step, setStep] = useState<CommandPhase>({ phase: 'idle' });
  const [comment, setComment] = useState('');
  const [rentalConfirmed, setRentalConfirmed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const rental = needsRentalConfirmation(car.status);
  const trimmed = comment.trim();
  const commentProblem =
    trimmed.length < commentMinLength ? 'short' : trimmed.length > commentMaxLength ? 'long' : null;

  const start = (command: CarCommand): void => {
    setStep({ phase: 'confirming', command });
    setSubmitted(false);
    setRentalConfirmed(false);
  };

  const send = (command: CarCommand): void => {
    setSubmitted(true);
    if (commentProblem || (rental && !rentalConfirmed)) return;
    setStep({ phase: 'sending', command });
    fleet
      .sendCommand(car.id, {
        command,
        comment: trimmed,
        confirm_active_rental: rental && rentalConfirmed,
      })
      .then(
        () => {
          setStep({ phase: 'done', command });
          setComment('');
        },
        (failure: unknown) => {
          const error = commandError(failure);
          if (error === 'signOut') {
            void signOut();
            return;
          }
          setStep({ phase: 'failed', command, error });
        },
      );
  };

  if (step.phase === 'confirming' || step.phase === 'sending') {
    const { command } = step;
    const sending = step.phase === 'sending';
    return (
      <form
        className="fleet-command"
        aria-label={t('fleet.confirm.title')}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          send(command);
        }}
      >
        <p>
          {t('fleet.confirm.text', { command: t(`fleet.command.${command}`), plate: car.plate })}
        </p>
        <label className="stacked-label">
          {t('fleet.confirm.comment')}
          <textarea
            className="field textarea"
            value={comment}
            maxLength={commentMaxLength}
            disabled={sending}
            onChange={(event) => {
              setComment(event.target.value);
            }}
          />
        </label>
        <p className="muted">{t('fleet.confirm.commentHint')}</p>
        {rental && (
          <label className="inline-label fleet-rental">
            <input
              type="checkbox"
              checked={rentalConfirmed}
              disabled={sending}
              onChange={(event) => {
                setRentalConfirmed(event.target.checked);
              }}
            />
            {t('fleet.confirm.rental')}
          </label>
        )}
        {submitted && commentProblem && (
          <p role="alert" className="list-error">
            {t(`fleet.confirm.problems.${commentProblem}`, {
              min: commentMinLength,
              max: commentMaxLength,
            })}
          </p>
        )}
        {submitted && rental && !rentalConfirmed && (
          <p role="alert" className="list-error">
            {t('fleet.confirm.problems.rental')}
          </p>
        )}
        {sending && (
          <p role="status" className="muted">
            {t('fleet.confirm.sending')}
          </p>
        )}
        <div className="zones-actions">
          <button type="submit" className="button button-primary" disabled={sending}>
            {t('fleet.confirm.send')}
          </button>
          <button
            type="button"
            className="button"
            disabled={sending}
            onClick={() => {
              setStep({ phase: 'idle' });
            }}
          >
            {t('fleet.confirm.cancel')}
          </button>
        </div>
      </form>
    );
  }

  return (
    <div className="fleet-commands">
      {step.phase === 'done' && (
        <p role="status" className="fleet-done">
          {t('fleet.result.done', { command: t(`fleet.command.${step.command}`) })}
        </p>
      )}
      {step.phase === 'failed' && (
        <p role="alert" className="list-error">
          {t(`fleet.commandErrors.${step.error.key}`, {
            command: t(`fleet.command.${step.command}`),
            reason: reasonText(step.error.reason),
            seconds: step.error.retryAfterSeconds ?? 60,
          })}
        </p>
      )}
      <div className="zones-actions" role="group" aria-label={t('fleet.commands')}>
        {carCommands.map((command) => (
          <button
            key={command}
            type="button"
            className="button"
            onClick={() => {
              start(command);
            }}
          >
            {t(`fleet.command.${command}`)}
          </button>
        ))}
      </div>
    </div>
  );
}
