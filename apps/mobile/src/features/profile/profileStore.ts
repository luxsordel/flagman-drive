import type { Language } from '../../i18n';
import type { ProfileApi, Profile, ProfileResult, ProfileUpdate } from './profileApi';
import type { ProfileError } from './profileErrors';

export interface ProfileState {
  /** Null until the first load succeeds. */
  profile: Profile | null;
  loading: boolean;
  /** Why the last load failed; the screen offers a retry. */
  loadError: ProfileError | null;
  /** A save is in flight: the screen locks every way of starting another one. */
  saving: boolean;
}

const initialState: ProfileState = {
  profile: null,
  loading: false,
  loadError: null,
  saving: false,
};

/**
 * The signed-in client's profile. Framework-free like KycStore, so the rules are unit-tested
 * without rendering anything.
 */
export class ProfileStore {
  private readonly api: ProfileApi;
  private readonly listeners = new Set<() => void>();
  private state: ProfileState = initialState;
  private loadingRequest: Promise<void> | null = null;
  // Bumped by reset(): an answer for the previous client must not land in the next one's state.
  private generation = 0;
  // Bumped by every kept save: a load that left before it may answer after it with older data.
  private saves = 0;
  private savesInFlight = 0;

  constructor(api: ProfileApi) {
    this.api = api;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getState = (): ProfileState => this.state;

  /** Parallel callers share one request. */
  load(): Promise<void> {
    if (this.loadingRequest !== null) return this.loadingRequest;
    const request = this.fetchProfile().finally(() => {
      // A request from before reset() must not clear the one that replaced it.
      if (this.loadingRequest === request) this.loadingRequest = null;
    });
    this.loadingRequest = request;
    return request;
  }

  /** The saved profile as the API returned it, so the form can show what was actually kept. */
  async save(update: ProfileUpdate): Promise<ProfileResult> {
    const generation = this.generation;
    this.savesInFlight += 1;
    this.setState({ ...this.state, saving: true });
    const result = await this.api.updateProfile(update).finally(() => {
      if (generation === this.generation) this.savesInFlight -= 1;
    });
    if (generation !== this.generation) return result;
    const saving = this.savesInFlight > 0;
    if (result.ok) {
      this.saves += 1;
      this.setState({ ...this.state, profile: result.profile, loadError: null, saving });
    } else {
      this.setState({ ...this.state, saving });
    }
    return result;
  }

  /** Forgets the profile at sign-out, so the next client never sees it. */
  reset(): void {
    this.generation += 1;
    this.loadingRequest = null;
    this.savesInFlight = 0;
    this.setState(initialState);
  }

  private async fetchProfile(): Promise<void> {
    const generation = this.generation;
    const saves = this.saves;
    this.setState({ ...this.state, loading: true, loadError: null });
    const result = await this.api.getProfile();
    if (generation !== this.generation) return;
    if (saves !== this.saves) {
      this.setState({ ...this.state, loading: false });
      return;
    }
    this.setState(
      result.ok
        ? { ...this.state, profile: result.profile, loading: false, loadError: null }
        : { ...this.state, loading: false, loadError: result.error },
    );
  }

  private setState(state: ProfileState): void {
    this.state = state;
    for (const listener of this.listeners) listener();
  }
}

/** The app speaks the language kept in the profile; the server will sync it across devices. */
export function followProfileLanguage(
  store: ProfileStore,
  apply: (language: Language) => void,
): () => void {
  return store.subscribe(() => {
    const { profile } = store.getState();
    if (profile !== null) apply(profile.language);
  });
}
