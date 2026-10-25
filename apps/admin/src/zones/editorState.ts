import type { Zone, ZoneKind } from './api';
import {
  maxZoneVertices,
  minZoneVertices,
  zoneNameMaxLength,
  zoneRing,
  type Ring,
} from './geometry';
import type { ZoneError } from './problems';

export interface ZoneDraft {
  name: string;
  kind: ZoneKind;
  /** Empty until the outline is drawn on the map. */
  ring: Ring;
}

/**
 * The zones page without the map: which zone is selected and what is being drawn or changed.
 * `create` and `edit` keep the zone the admin works on until it is saved or dropped.
 */
export type EditorState =
  | { mode: 'view'; selectedId: string | null }
  | {
      mode: 'create' | 'edit';
      /** The zone being changed; null while a new one is drawn. */
      zoneId: string | null;
      draft: ZoneDraft;
      /** The draft differs from what is saved: leaving it asks first. */
      dirty: boolean;
      saving: boolean;
      error: ZoneError | null;
    };

export type EditorAction =
  | { type: 'select'; id: string | null }
  | { type: 'startCreate' }
  | { type: 'startEdit'; zone: Zone }
  | { type: 'setName'; name: string }
  | { type: 'setKind'; kind: ZoneKind }
  | { type: 'setRing'; ring: Ring }
  | { type: 'cancel' }
  | { type: 'saveStarted' }
  | { type: 'saveFailed'; error: ZoneError }
  | { type: 'saved'; zone: Zone };

export const initialEditorState: EditorState = { mode: 'view', selectedId: null };

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'select':
      // Clicks on the map while drawing belong to the drawing, not to the selection.
      return state.mode === 'view' ? { mode: 'view', selectedId: action.id } : state;
    case 'startCreate':
      return {
        mode: 'create',
        zoneId: null,
        draft: { name: '', kind: 'allowed', ring: [] },
        dirty: false,
        saving: false,
        error: null,
      };
    case 'startEdit':
      return {
        mode: 'edit',
        zoneId: action.zone.id,
        draft: { name: action.zone.name, kind: action.zone.kind, ring: zoneRing(action.zone) },
        dirty: false,
        saving: false,
        error: null,
      };
    case 'setName':
    case 'setKind':
    case 'setRing': {
      if (state.mode === 'view' || state.saving) return state;
      const change =
        action.type === 'setName'
          ? { name: action.name }
          : action.type === 'setKind'
            ? { kind: action.kind }
            : { ring: action.ring };
      return { ...state, draft: { ...state.draft, ...change }, dirty: true, error: null };
    }
    case 'cancel':
      return state.mode === 'view' || state.saving
        ? state
        : { mode: 'view', selectedId: state.zoneId };
    case 'saveStarted':
      return state.mode === 'view' ? state : { ...state, saving: true, error: null };
    case 'saveFailed':
      return state.mode === 'view' ? state : { ...state, saving: false, error: action.error };
    case 'saved':
      return { mode: 'view', selectedId: action.zone.id };
  }
}

/** Why the draft cannot be saved yet, checked before asking the API; null when it can. */
export type DraftProblem =
  'noName' | 'longName' | 'noOutline' | 'tooFewVertices' | 'tooManyVertices';

export function draftProblem(draft: ZoneDraft): DraftProblem | null {
  const name = draft.name.trim();
  if (name === '') return 'noName';
  // The API counts code points, as Python does, not the UTF-16 units of `length`.
  if (Array.from(name).length > zoneNameMaxLength) return 'longName';
  if (draft.ring.length === 0) return 'noOutline';
  if (draft.ring.length < minZoneVertices) return 'tooFewVertices';
  if (draft.ring.length > maxZoneVertices) return 'tooManyVertices';
  return null;
}

/** The zones list after a save: a changed zone keeps its place, a new one goes last. */
export function withSavedZone(zones: readonly Zone[], saved: Zone): Zone[] {
  const index = zones.findIndex((zone) => zone.id === saved.id);
  if (index === -1) return [...zones, saved];
  return zones.map((zone) => (zone.id === saved.id ? saved : zone));
}
