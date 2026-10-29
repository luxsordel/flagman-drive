import type { LocationFix } from '../map/location';
import type {
  DamageSide,
  Debt,
  Inspection,
  InspectionStage,
  PhotoUpload,
  Rental,
  RentalApi,
  RentalCommand,
  RentalResult,
} from './rentalApi';
import { isStale, type RentalError } from './rentalErrors';
import { clockOffsetMs, isActive, isOnTrip } from './rentalTime';

export type RentalAction =
  'reserve' | 'cancel' | 'damage' | 'submit' | 'park' | 'resume' | 'finish' | 'pay' | RentalCommand;

export interface RentalState {
  /** False until the server has said whether the client has an active rental. */
  restored: boolean;
  /** The active rental, or the one that has just ended, until the client dismisses it. */
  rental: Rental | null;
  /** An earlier rental opened by its id while another one is active, e.g. from a notification. */
  viewed: Rental | null;
  /** Server clock minus the phone's, for the countdowns (see clockOffsetMs). */
  offsetMs: number;
  /** The inspection of the rental's stage, before or after the trip; loaded by its screen. */
  inspection: Inspection | null;
  /** What the client owes for earlier rentals; empty when nothing. */
  debts: Debt[];
  /** What is being sent right now: its button waits, the others too. */
  pending: RentalAction | null;
  /** Photos of the inspection still on their way: it is not submitted before they arrive. */
  uploading: number;
  /** Why the last action or load failed. */
  error: RentalError | null;
}

const initialState: RentalState = {
  restored: false,
  rental: null,
  viewed: null,
  offsetMs: 0,
  inspection: null,
  debts: [],
  pending: null,
  uploading: 0,
  error: null,
};

/**
 * The client's rental, restored from the server at launch so the app comes back to it after a
 * restart. Framework-free like KycStore. Only the server decides: every answer replaces the state.
 */
export class RentalStore {
  private readonly api: RentalApi;
  private readonly locate: () => Promise<LocationFix>;
  private readonly now: () => number;
  private readonly listeners = new Set<() => void>();
  private state: RentalState = initialState;
  // Bumped by reset(): an answer for the previous client must not land in the next one's state.
  private generation = 0;

  constructor(api: RentalApi, locate: () => Promise<LocationFix>, now: () => number = Date.now) {
    this.api = api;
    this.locate = locate;
    this.now = now;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getState = (): RentalState => this.state;

  /** Asks the server for the active rental; safe to call on every launch and return to the app. */
  async restore(): Promise<void> {
    const generation = this.generation;
    const result = await this.api.current();
    if (generation !== this.generation) return;
    if (result.ok) {
      const known = this.state.rental;
      if (result.value !== null) this.acceptRental(result.value);
      // Ended while the app was in the background: the client is told how, not "not found".
      else if (known !== null && isActive(known)) await this.refresh();
      // One that ended before stays on screen until dismissed.
      else if (known === null) this.setState({ ...this.state, inspection: null });
      if (generation !== this.generation) return;
      this.setState({ ...this.state, restored: true, error: null });
      await this.loadDebts();
    } else {
      // The map still works without it; the next restore tries again.
      this.setState({ ...this.state, restored: true, error: result.error });
    }
  }

  /**
   * Shows a rental by its id, e.g. the one a tapped notification is about. An active rental stays
   * in place: an old notification must not take the client's reservation off the map.
   */
  async open(id: string): Promise<void> {
    if (this.state.rental?.id === id) {
      await this.refresh();
      return;
    }
    const generation = this.generation;
    const result = await this.api.get(id);
    if (generation !== this.generation) return;
    if (result.ok) this.acceptOpened(result.value);
    else if (result.error !== null) this.setState({ ...this.state, error: result.error });
  }

  async loadDebts(): Promise<void> {
    const generation = this.generation;
    const result = await this.api.debts();
    if (generation !== this.generation) return;
    // The map works without them: a failure keeps what was known.
    if (result.ok) this.setState({ ...this.state, debts: result.value });
  }

  /** Reloads the rental: its deadlines and status may have changed on the server. */
  async refresh(): Promise<void> {
    const rental = this.state.rental;
    if (rental === null) return;
    const generation = this.generation;
    const result = await this.api.get(rental.id);
    if (generation !== this.generation) return;
    if (result.ok) this.acceptRental(result.value);
    else if (result.error !== null) this.setState({ ...this.state, error: result.error });
  }

  reserve(carId: string): Promise<RentalError | null> {
    return this.run(
      'reserve',
      () => this.api.reserve(carId),
      (rental) => {
        this.acceptRental(rental);
        this.setState({ ...this.state, inspection: null });
      },
    );
  }

  async cancel(): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null) return null;
    const error = await this.run(
      'cancel',
      () => this.api.cancel(rental.id),
      (cancelled) => {
        this.acceptRental(cancelled);
      },
    );
    // A paid wait whose charge was declined is a debt the map has to show.
    if (error === null) await this.loadDebts();
    return error;
  }

  async loadInspection(): Promise<void> {
    const rental = this.state.rental;
    if (rental === null) return;
    const generation = this.generation;
    const result = await this.api.inspection(rental.id, stageOf(rental));
    if (generation !== this.generation) return;
    if (result.ok) this.setState({ ...this.state, inspection: result.value, error: null });
    else if (result.error !== null) this.setState({ ...this.state, error: result.error });
  }

  uploadPhoto(photoUri: string, onProgress?: (sent: number) => void): PhotoUpload {
    const rental = this.state.rental;
    if (rental === null) {
      return { done: Promise.resolve({ ok: false, error: { kind: 'notFound' } }), abort: () => {} };
    }
    const generation = this.generation;
    const upload = this.api.uploadPhoto(rental.id, stageOf(rental), photoUri, onProgress);
    this.setState({ ...this.state, uploading: this.state.uploading + 1 });
    return {
      done: upload.done.then(async (result) => {
        if (generation !== this.generation) return result;
        this.setState({ ...this.state, uploading: Math.max(0, this.state.uploading - 1) });
        if (result.ok) this.setState({ ...this.state, inspection: result.value });
        else if (result.error !== null && isStale(result.error)) await this.refresh();
        return result;
      }),
      abort: upload.abort,
    };
  }

  addDamage(side: DamageSide, comment: string): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null) return Promise.resolve(null);
    return this.run(
      'damage',
      () => this.api.addDamage(rental.id, stageOf(rental), side, comment),
      (inspection) => {
        this.setState({ ...this.state, inspection });
      },
    );
  }

  submitInspection(): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null) return Promise.resolve(null);
    const stage = stageOf(rental);
    return this.run(
      'submit',
      () => this.api.submitInspection(rental.id, stage),
      (inspection) => {
        const current = this.state.rental;
        const submitted =
          stage === 'before'
            ? { inspection_submitted: true }
            : { after_inspection_submitted: true };
        this.setState({
          ...this.state,
          inspection,
          rental: current === null ? null : { ...current, ...submitted },
        });
      },
    );
  }

  /** Parked time costs the parked price; the server answers a repeat as it is. */
  park(): Promise<RentalError | null> {
    return this.change('park', (id) => this.api.park(id));
  }

  resume(): Promise<RentalError | null> {
    return this.change('resume', (id) => this.api.resume(id));
  }

  /** Checks and locks the car on the server; safe to repeat after a lost answer. */
  async finish(): Promise<RentalError | null> {
    const error = await this.change('finish', (id) => this.api.finish(id));
    if (error === null) await this.loadDebts();
    return error;
  }

  /** Pays the rental on the receipt screen: the one opened by its id, or the current one. */
  async payDebt(): Promise<RentalError | null> {
    const target = this.state.viewed ?? this.state.rental;
    if (target === null) return null;
    const error = await this.run(
      'pay',
      () => this.api.payDebt(target.id),
      (paid) => {
        this.acceptOpened(paid);
      },
    );
    await this.loadDebts();
    return error;
  }

  /** Locates the client first: the server opens the car only within reach of it. */
  async command(command: RentalCommand): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null || this.state.pending !== null) return null;
    const generation = this.generation;
    this.setState({ ...this.state, pending: command, error: null });
    const fix = await this.locate();
    if (generation !== this.generation) return null;
    if (fix.kind !== 'located') {
      const error: RentalError = {
        kind: fix.kind === 'denied' ? 'locationDenied' : 'locationUnavailable',
      };
      this.setState({ ...this.state, pending: null, error });
      return error;
    }
    this.setState({ ...this.state, pending: null });
    return this.run(
      command,
      () => this.api.command(rental.id, command, fix.location),
      (sent) => {
        this.acceptRental(sent);
      },
    );
  }

  /** Puts away a rental that has ended, back to the map. */
  dismiss(): void {
    const rental = this.state.rental;
    if (rental !== null && isActive(rental)) {
      this.setState({ ...this.state, viewed: null, error: null });
      return;
    }
    this.setState({ ...this.state, rental: null, viewed: null, inspection: null, error: null });
  }

  /** Forgets everything at sign-out, so the next client never sees it. */
  reset(): void {
    this.generation += 1;
    this.setState(initialState);
  }

  private change(
    action: RentalAction,
    request: (id: string) => Promise<RentalResult<Rental>>,
  ): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null) return Promise.resolve(null);
    return this.run(
      action,
      () => request(rental.id),
      (changed) => {
        this.acceptRental(changed);
      },
    );
  }

  private async run<T>(
    action: RentalAction,
    request: () => Promise<RentalResult<T>>,
    accept: (value: T) => void,
  ): Promise<RentalError | null> {
    if (this.state.pending !== null) return null;
    const generation = this.generation;
    this.setState({ ...this.state, pending: action, error: null });
    const result = await request();
    if (generation !== this.generation) return null;
    this.setState({ ...this.state, pending: null });
    if (result.ok) {
      accept(result.value);
      return null;
    }
    const error = result.error ?? { kind: 'unknown' };
    this.setState({ ...this.state, error });
    // Expired or started meanwhile, or the car may have done it: show what the server has now.
    if (isStale(error)) await this.refresh();
    return error;
  }

  private acceptOpened(rental: Rental): void {
    const active = this.state.rental;
    if (active !== null && active.id !== rental.id && isActive(active)) {
      this.setState({ ...this.state, viewed: rental, error: null });
    } else {
      this.acceptRental(rental);
    }
  }

  private acceptRental(rental: Rental): void {
    this.setState({
      ...this.state,
      rental,
      offsetMs: clockOffsetMs(rental.as_of, this.now()),
      error: null,
    });
  }

  private setState(state: RentalState): void {
    this.state = state;
    for (const listener of this.listeners) listener();
  }
}

/** Before the trip the car is inspected at the reservation, after it at the end of the trip. */
export function stageOf(rental: Rental): InspectionStage {
  return isOnTrip(rental) ? 'after' : 'before';
}
