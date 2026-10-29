import type { LocationFix } from '../map/location';
import type {
  DamageSide,
  Inspection,
  PhotoUpload,
  Rental,
  RentalApi,
  RentalCommand,
  RentalResult,
} from './rentalApi';
import { isStale, type RentalError } from './rentalErrors';
import { clockOffsetMs, isActive } from './rentalTime';

export type RentalAction = 'reserve' | 'cancel' | 'damage' | 'submit' | RentalCommand;

export interface RentalState {
  /** False until the server has said whether the client has an active rental. */
  restored: boolean;
  /** The active rental, or the one that has just ended, until the client dismisses it. */
  rental: Rental | null;
  /** Server clock minus the phone's, for the countdowns (see clockOffsetMs). */
  offsetMs: number;
  /** The inspection before the trip; loaded by the inspection screen. */
  inspection: Inspection | null;
  /** What is being sent right now: its button waits, the others too. */
  pending: RentalAction | null;
  /** Why the last action or load failed. */
  error: RentalError | null;
}

const initialState: RentalState = {
  restored: false,
  rental: null,
  offsetMs: 0,
  inspection: null,
  pending: null,
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
      // A rental that ended while the app was closed stays on screen until dismissed.
      const ended = this.state.rental !== null && !isActive(this.state.rental);
      if (result.value !== null) this.acceptRental(result.value);
      else if (!ended) this.setState({ ...this.state, rental: null, inspection: null });
      this.setState({ ...this.state, restored: true, error: null });
    } else {
      // The map still works without it; the next restore tries again.
      this.setState({ ...this.state, restored: true, error: result.error });
    }
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

  cancel(): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null) return Promise.resolve(null);
    return this.run(
      'cancel',
      () => this.api.cancel(rental.id),
      (cancelled) => {
        this.acceptRental(cancelled);
      },
    );
  }

  async loadInspection(): Promise<void> {
    const rental = this.state.rental;
    if (rental === null) return;
    const generation = this.generation;
    const result = await this.api.inspection(rental.id);
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
    const upload = this.api.uploadPhoto(rental.id, photoUri, onProgress);
    return {
      done: upload.done.then(async (result) => {
        if (generation !== this.generation) return result;
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
      () => this.api.addDamage(rental.id, side, comment),
      (inspection) => {
        this.setState({ ...this.state, inspection });
      },
    );
  }

  submitInspection(): Promise<RentalError | null> {
    const rental = this.state.rental;
    if (rental === null) return Promise.resolve(null);
    return this.run(
      'submit',
      () => this.api.submitInspection(rental.id),
      (inspection) => {
        const current = this.state.rental;
        this.setState({
          ...this.state,
          inspection,
          rental: current === null ? null : { ...current, inspection_submitted: true },
        });
      },
    );
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
      const error: RentalError = { kind: fix.kind === 'denied' ? 'locationDenied' : 'network' };
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
    if (rental !== null && isActive(rental)) return;
    this.setState({ ...this.state, rental: null, inspection: null, error: null });
  }

  /** Forgets everything at sign-out, so the next client never sees it. */
  reset(): void {
    this.generation += 1;
    this.setState(initialState);
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
