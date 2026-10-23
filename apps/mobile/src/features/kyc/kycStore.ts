import type { DocumentKind, KycApi, KycApplication, KycResult, UploadHandle } from './kycApi';
import type { KycError } from './kycErrors';

export interface KycState {
  /** Null until the first load succeeds. */
  application: KycApplication | null;
  loading: boolean;
  /** Why the last load failed; the screens offer a retry. */
  loadError: KycError | null;
}

const initialState: KycState = { application: null, loading: false, loadError: null };

/**
 * The signed-in client's KYC application, shared by every screen that shows its status.
 * Framework-free like AuthSession, so the rules are unit-tested without rendering anything.
 */
export class KycStore {
  private readonly api: KycApi;
  private readonly listeners = new Set<() => void>();
  private state: KycState = initialState;
  private loadingRequest: Promise<void> | null = null;
  // Bumped by reset(): an answer for the previous client must not land in the next one's state.
  private generation = 0;

  constructor(api: KycApi) {
    this.api = api;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getState = (): KycState => this.state;

  /** Parallel callers share one request. */
  load(): Promise<void> {
    this.loadingRequest ??= this.fetchApplication().finally(() => {
      this.loadingRequest = null;
    });
    return this.loadingRequest;
  }

  upload(kind: DocumentKind, photoUri: string, onProgress?: (sent: number) => void): UploadHandle {
    const generation = this.generation;
    const handle = this.api.upload(kind, photoUri, onProgress);
    return {
      done: handle.done.then(async (result) => {
        this.accept(result, generation);
        // Sent for review meanwhile, e.g. from another device: the screens follow the new status.
        if (!result.ok && result.error?.kind === 'locked') await this.load();
        return result;
      }),
      abort: handle.abort,
    };
  }

  async submit(): Promise<KycError | null> {
    const generation = this.generation;
    const result = await this.api.submit();
    this.accept(result, generation);
    if (result.ok) return null;
    // Someone else changed the application (an operator, another device): show what it is now.
    if (result.error?.kind === 'locked' || result.error?.kind === 'incomplete') await this.load();
    return result.error ?? { kind: 'unknown' };
  }

  /** Forgets the application at sign-out, so the next client never sees it. */
  reset(): void {
    this.generation += 1;
    this.loadingRequest = null;
    this.setState(initialState);
  }

  private async fetchApplication(): Promise<void> {
    const generation = this.generation;
    this.setState({ ...this.state, loading: true, loadError: null });
    const result = await this.api.getApplication();
    if (generation !== this.generation) return;
    this.setState(
      result.ok
        ? { application: result.application, loading: false, loadError: null }
        : { ...this.state, loading: false, loadError: result.error ?? { kind: 'unknown' } },
    );
  }

  private accept(result: KycResult, generation: number): void {
    if (result.ok && generation === this.generation) {
      this.setState({ ...this.state, application: result.application, loadError: null });
    }
  }

  private setState(state: KycState): void {
    this.state = state;
    for (const listener of this.listeners) listener();
  }
}
