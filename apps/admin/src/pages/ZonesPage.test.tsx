import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError, NetworkError } from '../api/client';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';
import { fakeZonesApi, zone } from '../test/zones';
import type { ZoneMapProps } from '../zones/ZoneMap';

const triangle: [number, number][] = [
  [27.55, 53.89],
  [27.57, 53.89],
  [27.57, 53.91],
];
const bowTie: [number, number][] = [
  [27.55, 53.89],
  [27.57, 53.91],
  [27.57, 53.89],
  [27.55, 53.91],
];

// jsdom has no WebGL: the map is replaced by buttons that do what clicks on the real one would.
vi.mock('../zones/ZoneMap', () => ({
  ZoneMap: ({ zones, editing, onSelect, onRingChange, label }: ZoneMapProps) => (
    <div role="region" aria-label={label} data-editing={editing ? (editing.zoneId ?? 'new') : ''}>
      {zones.map((shown) => (
        <button
          key={shown.id}
          type="button"
          onClick={() => {
            onSelect(shown.id);
          }}
        >
          {`map: ${shown.name}`}
        </button>
      ))}
      <button
        type="button"
        onClick={() => {
          onRingChange(triangle);
        }}
      >
        map: draw triangle
      </button>
      <button
        type="button"
        onClick={() => {
          onRingChange(bowTie);
        }}
      >
        map: draw bow tie
      </button>
    </div>
  ),
}));

const closedTriangle = [[...triangle, triangle[0]]];

/** A zone in the list beside the map, not its stand-in on the map. */
async function listedZone(name: string): Promise<HTMLElement> {
  const list = await screen.findByRole('list', { name: ru.zones.list });
  return within(list).getByRole('button', { name: new RegExp(name) });
}

describe('zones page', () => {
  it('shows every zone to an operator, without changing them', async () => {
    const user = userEvent.setup();
    const centre = zone({ name: 'Центр' });
    const station = zone({ name: 'Вокзал', kind: 'forbidden' });
    renderAt('/zones', testStaff('operator'), { zones: fakeZonesApi([centre, station]) });

    const list = await screen.findByRole('list', { name: ru.zones.list });
    expect(within(list).getByText('Центр')).toBeDefined();
    expect(within(list).getByText(ru.zones.kind.forbidden)).toBeDefined();
    expect(screen.getByRole('region', { name: ru.zones.map })).toBeDefined();
    expect(screen.queryByRole('button', { name: ru.zones.create })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'map: Вокзал' }));

    expect(screen.getByRole('heading', { name: 'Вокзал' })).toBeDefined();
    expect(screen.queryByRole('button', { name: ru.zones.edit })).toBeNull();
    expect(screen.queryByRole('button', { name: ru.zones.disable })).toBeNull();
  });

  it('is open to a technician too', async () => {
    renderAt('/zones', testStaff('technician'), { zones: fakeZonesApi([zone({ name: 'Центр' })]) });

    expect(await screen.findByText('Центр')).toBeDefined();
  });

  it('lets an admin draw a new zone', async () => {
    const user = userEvent.setup();
    const zones = fakeZonesApi();
    renderAt('/zones', testStaff('admin'), { zones });

    await user.click(await screen.findByRole('button', { name: ru.zones.create }));
    expect(screen.getByText(ru.zones.form.drawHint)).toBeDefined();
    expect(screen.getByRole('region', { name: ru.zones.map }).dataset.editing).toBe('new');

    await user.type(screen.getByLabelText(ru.zones.form.name), '  Центр ');
    await user.click(screen.getByLabelText(ru.zones.kind.forbidden));
    await user.click(screen.getByRole('button', { name: 'map: draw triangle' }));
    await user.click(screen.getByRole('button', { name: ru.zones.form.save }));

    expect(zones.create).toHaveBeenCalledWith({
      name: 'Центр',
      kind: 'forbidden',
      polygon: { type: 'Polygon', coordinates: closedTriangle },
    });
    const list = await screen.findByRole('list', { name: ru.zones.list });
    expect(within(list).getByText('Центр')).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Центр' })).toBeDefined();
  });

  it('does not send a zone without a name or an outline', async () => {
    const user = userEvent.setup();
    const zones = fakeZonesApi();
    renderAt('/zones', testStaff('admin'), { zones });

    await user.click(await screen.findByRole('button', { name: ru.zones.create }));
    await user.click(screen.getByRole('button', { name: ru.zones.form.save }));
    expect(screen.getByRole('alert').textContent).toBe(ru.zones.form.problems.noName);

    await user.type(screen.getByLabelText(ru.zones.form.name), 'Центр');
    expect(screen.getByRole('alert').textContent).toBe(ru.zones.form.problems.noOutline);
    expect(zones.create).not.toHaveBeenCalled();
  });

  it('explains a polygon the API refuses and keeps the draft', async () => {
    const user = userEvent.setup();
    const zones = fakeZonesApi();
    zones.create.mockRejectedValueOnce(new ApiError(422, 'zone-polygon-self-intersecting'));
    renderAt('/zones', testStaff('admin'), { zones });

    await user.click(await screen.findByRole('button', { name: ru.zones.create }));
    await user.type(screen.getByLabelText(ru.zones.form.name), 'Центр');
    await user.click(screen.getByRole('button', { name: 'map: draw bow tie' }));
    await user.click(screen.getByRole('button', { name: ru.zones.form.save }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.zones.errors.selfIntersecting);
    expect(screen.getByLabelText<HTMLInputElement>(ru.zones.form.name).value).toBe('Центр');

    await user.click(screen.getByRole('button', { name: 'map: draw triangle' }));
    expect(screen.queryByRole('alert')).toBeNull();
    await user.click(screen.getByRole('button', { name: ru.zones.form.save }));
    expect(await screen.findByRole('list', { name: ru.zones.list })).toBeDefined();
  });

  it('changes a zone', async () => {
    const user = userEvent.setup();
    const centre = zone({ name: 'Центр' });
    const zones = fakeZonesApi([centre]);
    renderAt('/zones', testStaff('admin'), { zones });

    await user.click(await listedZone('Центр'));
    await user.click(screen.getByRole('button', { name: ru.zones.edit }));
    expect(screen.getByRole('region', { name: ru.zones.map }).dataset.editing).toBe(centre.id);
    const name = screen.getByLabelText(ru.zones.form.name);
    await user.clear(name);
    await user.type(name, 'Центр-2');
    await user.click(screen.getByRole('button', { name: ru.zones.form.save }));

    expect(zones.update).toHaveBeenCalledWith(centre.id, {
      name: 'Центр-2',
      kind: 'allowed',
      polygon: centre.polygon,
    });
    expect(await screen.findByRole('heading', { name: 'Центр-2' })).toBeDefined();
    expect(screen.queryByText('Центр')).toBeNull();
  });

  it('asks before dropping unsaved changes', async () => {
    const user = userEvent.setup();
    renderAt('/zones', testStaff('admin'), { zones: fakeZonesApi([zone({ name: 'Центр' })]) });

    await user.click(await listedZone('Центр'));
    await user.click(screen.getByRole('button', { name: ru.zones.edit }));
    await user.type(screen.getByLabelText(ru.zones.form.name), '!');
    await user.click(screen.getByRole('button', { name: ru.zones.form.cancel }));

    const confirm = screen.getByRole('group', { name: ru.zones.cancelConfirm.title });
    await user.click(within(confirm).getByRole('button', { name: ru.zones.cancelConfirm.keep }));
    expect(screen.getByLabelText<HTMLInputElement>(ru.zones.form.name).value).toBe('Центр!');

    await user.click(screen.getByRole('button', { name: ru.zones.form.cancel }));
    await user.click(screen.getByRole('button', { name: ru.zones.cancelConfirm.discard }));
    expect(screen.getByRole('heading', { name: 'Центр' })).toBeDefined();
  });

  it('disables a zone after a confirmation', async () => {
    const user = userEvent.setup();
    const centre = zone({ name: 'Центр' });
    const zones = fakeZonesApi([centre]);
    renderAt('/zones', testStaff('admin'), { zones });

    await user.click(await listedZone('Центр'));
    await user.click(screen.getByRole('button', { name: ru.zones.disable }));
    expect(zones.disable).not.toHaveBeenCalled();
    const confirm = screen.getByRole('group', { name: ru.zones.disableConfirm.title });
    await user.click(
      within(confirm).getByRole('button', { name: ru.zones.disableConfirm.confirm }),
    );

    expect(zones.disable).toHaveBeenCalledWith(centre.id);
    expect(await screen.findByText(ru.zones.empty)).toBeDefined();
  });

  it('offers to retry when the zones cannot be loaded', async () => {
    const user = userEvent.setup();
    const zones = fakeZonesApi([zone({ name: 'Центр' })]);
    zones.list.mockRejectedValueOnce(new NetworkError(new TypeError('offline')));
    renderAt('/zones', testStaff('admin'), { zones });

    await user.click(await screen.findByRole('button', { name: ru.zones.retry }));

    expect(await screen.findByText('Центр')).toBeDefined();
  });

  it('signs out when the session is over', async () => {
    const zones = fakeZonesApi();
    zones.list.mockRejectedValueOnce(new ApiError(401, 'access-token-invalid'));
    const { api } = renderAt('/zones', testStaff('admin'), { zones });

    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(api.logout).toHaveBeenCalledOnce();
  });
});
