import { describe, expect, it } from 'vitest';

import { zone } from '../test/zones';
import {
  draftProblem,
  editorReducer,
  initialEditorState,
  withSavedZone,
  type EditorAction,
  type EditorState,
} from './editorState';
import { maxZoneVertices, type Ring } from './geometry';

const triangle: Ring = [
  [27.55, 53.89],
  [27.57, 53.89],
  [27.57, 53.91],
];

const run = (...actions: EditorAction[]): EditorState =>
  actions.reduce(editorReducer, initialEditorState);

describe('zone editor state', () => {
  it('selects a zone only while nothing is edited', () => {
    expect(run({ type: 'select', id: 'a' })).toEqual({ mode: 'view', selectedId: 'a' });
    expect(run({ type: 'startCreate' }, { type: 'select', id: 'a' })).toMatchObject({
      mode: 'create',
    });
  });

  it('starts a new zone empty and allowed', () => {
    expect(run({ type: 'startCreate' })).toEqual({
      mode: 'create',
      zoneId: null,
      draft: { name: '', kind: 'allowed', ring: [] },
      dirty: false,
      saving: false,
      error: null,
    });
  });

  it('starts editing from the saved zone without its closing point', () => {
    const saved = zone({ name: 'Центр', kind: 'forbidden' });

    const state = run({ type: 'startEdit', zone: saved });

    expect(state).toMatchObject({
      mode: 'edit',
      zoneId: saved.id,
      draft: { name: 'Центр', kind: 'forbidden' },
      dirty: false,
    });
    if (state.mode === 'view') throw new Error('not editing');
    expect(state.draft.ring).toHaveLength(4);
  });

  it('marks the draft changed and clears the last error', () => {
    const state = run(
      { type: 'startCreate' },
      { type: 'saveStarted' },
      { type: 'saveFailed', error: 'selfIntersecting' },
      { type: 'setRing', ring: triangle },
    );

    expect(state).toMatchObject({ dirty: true, error: null, draft: { ring: triangle } });
  });

  it('ignores changes while the save is on its way', () => {
    const state = run(
      { type: 'startCreate' },
      { type: 'saveStarted' },
      { type: 'setName', name: 'x' },
    );

    expect(state).toMatchObject({ saving: true, draft: { name: '' } });
  });

  it('keeps the draft after a failed save', () => {
    const state = run(
      { type: 'startCreate' },
      { type: 'setName', name: 'Центр' },
      { type: 'saveStarted' },
      { type: 'saveFailed', error: 'outsideArea' },
    );

    expect(state).toMatchObject({ saving: false, error: 'outsideArea', draft: { name: 'Центр' } });
  });

  it('selects the saved zone, and the edited one after cancelling', () => {
    const saved = zone();

    expect(run({ type: 'startCreate' }, { type: 'saved', zone: saved })).toEqual({
      mode: 'view',
      selectedId: saved.id,
    });
    expect(run({ type: 'startEdit', zone: saved }, { type: 'cancel' })).toEqual({
      mode: 'view',
      selectedId: saved.id,
    });
  });

  it('cannot cancel while saving', () => {
    expect(run({ type: 'startCreate' }, { type: 'saveStarted' }, { type: 'cancel' })).toMatchObject(
      { mode: 'create', saving: true },
    );
  });
});

describe('draft problems', () => {
  it('asks for a name and an outline', () => {
    expect(draftProblem({ name: '  ', kind: 'allowed', ring: triangle })).toBe('noName');
    expect(draftProblem({ name: 'Центр', kind: 'allowed', ring: [] })).toBe('noOutline');
    expect(draftProblem({ name: 'Центр', kind: 'allowed', ring: triangle.slice(0, 2) })).toBe(
      'tooFewVertices',
    );
    expect(draftProblem({ name: 'Центр', kind: 'allowed', ring: triangle })).toBeNull();
  });

  it('counts the name in characters as the API does', () => {
    // 100 emoji are 200 UTF-16 units but 100 characters, which the API accepts.
    expect(draftProblem({ name: '🚗'.repeat(100), kind: 'allowed', ring: triangle })).toBeNull();
    expect(draftProblem({ name: 'a'.repeat(101), kind: 'allowed', ring: triangle })).toBe(
      'longName',
    );
  });

  it('stops an outline with more vertices than the API takes', () => {
    const ring = Array.from({ length: maxZoneVertices + 1 }, (_, index): [number, number] => [
      27.5 + index / 10_000,
      53.9,
    ]);

    expect(draftProblem({ name: 'Центр', kind: 'allowed', ring })).toBe('tooManyVertices');
  });
});

describe('zones after a save', () => {
  it('replaces a changed zone in place and puts a new one last', () => {
    const first = zone();
    const second = zone();
    const changed = { ...first, name: 'Changed' };
    const created = zone();

    expect(withSavedZone([first, second], changed)).toEqual([changed, second]);
    expect(withSavedZone([first, second], created)).toEqual([first, second, created]);
  });
});
