import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import type { FleetMapProps } from '../fleet/FleetMap';
import ru from '../i18n/ru.json';
import { testStaff } from '../test/auth';
import {
  carsChanged,
  commandNotice,
  fakeFleetApi,
  fakeFleetStream,
  fleetCar,
  snapshot,
} from '../test/fleet';
import { renderAt } from '../test/render';
import { fakeZonesApi, zone } from '../test/zones';

// jsdom has no WebGL: the map is replaced by buttons that do what clicks on the real one would.
vi.mock('../fleet/FleetMap', () => ({
  FleetMap: ({ cars, zones, isStale, onSelect, label }: FleetMapProps) => (
    <div role="region" aria-label={label} data-zones={zones.length}>
      {cars.map((car) => (
        <button
          key={car.id}
          type="button"
          data-stale={isStale(car)}
          onClick={() => {
            onSelect(car.id);
          }}
        >
          {`map: ${car.plate}`}
        </button>
      ))}
    </div>
  ),
}));

function renderFleet(role: Parameters<typeof testStaff>[0] = 'operator') {
  const stream = fakeFleetStream();
  const fleet = fakeFleetApi();
  const zones = fakeZonesApi([zone()]);
  const rendered = renderAt('/fleet', testStaff(role), {
    fleetStream: stream.open,
    fleet,
    zones,
  });
  return { ...rendered, stream, fleet };
}

async function opened(stream: ReturnType<typeof fakeFleetStream>) {
  await screen.findByRole('heading', { level: 1, name: ru.nav.fleet });
  await vi.waitFor(() => {
    expect(stream.open).toHaveBeenCalled();
  });
}

const list = () => screen.getByRole('list', { name: ru.fleet.list });
const listed = (plate: string) =>
  within(list()).getByRole('button', { name: (name) => name.includes(plate) });

describe('fleet page', () => {
  it('shows the fleet from the snapshot on the map and in the list', async () => {
    const { stream } = renderFleet();
    await opened(stream);
    expect(screen.getByText(ru.fleet.connection.connecting)).toBeDefined();

    stream.setStatus('live');
    stream.send(snapshot([fleetCar({ plate: '1111 AB-7' }), fleetCar({ plate: '2222 AB-7' })]));

    expect(listed('1111 AB-7')).toBeDefined();
    expect(listed('2222 AB-7')).toBeDefined();
    expect(screen.getByRole('button', { name: 'map: 1111 AB-7' })).toBeDefined();
    expect(screen.queryByText(ru.fleet.connection.connecting)).toBeNull();
    expect(screen.getByRole('region', { name: ru.fleet.map }).dataset.zones).toBe('1');
  });

  it('applies changes as they come', async () => {
    const { stream } = renderFleet();
    await opened(stream);
    const car = fleetCar({ plate: '1111 AB-7' });
    stream.send(snapshot([car]));
    await userEvent.setup().click(listed('1111 AB-7'));

    stream.send(carsChanged([{ ...car, status: 'service' }]));

    const card = screen.getByRole('region', { name: '1111 AB-7' });
    expect(within(card).getByText(ru.fleet.status.service)).toBeDefined();
  });

  it('filters the cars by status and low battery', async () => {
    const user = userEvent.setup();
    const { stream } = renderFleet();
    await opened(stream);
    stream.send(
      snapshot([
        fleetCar({ plate: '1111 AB-7', state: { battery_pct: 10 } }),
        fleetCar({ plate: '2222 AB-7', status: 'service' }),
      ]),
    );

    await user.click(screen.getByRole('checkbox', { name: ru.fleet.filters.lowBattery }));
    expect(within(list()).getAllByRole('button')).toHaveLength(1);
    expect(listed('1111 AB-7')).toBeDefined();

    await user.click(screen.getByRole('checkbox', { name: ru.fleet.filters.lowBattery }));
    await user.click(screen.getByRole('checkbox', { name: ru.fleet.status.service }));
    expect(within(list()).getAllByRole('button')).toHaveLength(1);
    expect(listed('2222 AB-7')).toBeDefined();
  });

  it('opens the card of a car picked on the map and marks it in the list', async () => {
    const user = userEvent.setup();
    const { stream } = renderFleet();
    await opened(stream);
    stream.send(snapshot([fleetCar({ plate: '1111 AB-7', state: { battery_pct: 64 } })]));

    await user.click(screen.getByRole('button', { name: 'map: 1111 AB-7' }));

    const card = screen.getByRole('region', { name: '1111 AB-7' });
    expect(within(card).getByText('Geely Geometry C')).toBeDefined();
    expect(within(card).getByText(/64 %/)).toBeDefined();
    expect(listed('1111 AB-7').getAttribute('aria-pressed')).toBe('true');
  });

  it('marks a car without fresh telemetry', async () => {
    const user = userEvent.setup();
    const { stream } = renderFleet();
    await opened(stream);
    const old = new Date(Date.now() - 10 * 60_000).toISOString();
    stream.send(snapshot([fleetCar({ plate: '1111 AB-7', state: { recorded_at: old } })]));

    expect(screen.getByRole('button', { name: 'map: 1111 AB-7' }).dataset.stale).toBe('true');
    expect(listed('1111 AB-7').textContent).toContain(ru.fleet.stale);
    await user.click(listed('1111 AB-7'));
    expect(screen.getByRole('note').textContent).toBe(ru.fleet.card.staleTelemetry);
  });

  it('warns that the data may be out of date while it reconnects', async () => {
    const { stream } = renderFleet();
    await opened(stream);
    stream.send(snapshot([fleetCar({ plate: '1111 AB-7' })]));
    stream.setStatus('live');

    stream.setStatus('reconnecting');

    expect(screen.getByRole('status').textContent).toBe(ru.fleet.connection.reconnecting);
    expect(listed('1111 AB-7')).toBeDefined();
    stream.setStatus('live');
    expect(screen.queryByText(ru.fleet.connection.reconnecting)).toBeNull();
  });

  it('signs out when the stream says the session is over', async () => {
    const { stream, api } = renderFleet();
    await opened(stream);

    stream.setStatus('signedOut');

    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(api.logout).toHaveBeenCalled();
  });

  it('closes the stream when the page is left', async () => {
    const { stream, router } = renderFleet();
    await opened(stream);

    await router.navigate('/');

    await vi.waitFor(() => {
      expect(stream.close).toHaveBeenCalled();
    });
  });
});

describe('manual commands', () => {
  async function cardOf(plate: string, carOverrides: Parameters<typeof fleetCar>[0] = {}) {
    const user = userEvent.setup();
    const page = renderFleet();
    await opened(page.stream);
    const car = fleetCar({ plate, ...carOverrides });
    page.stream.setStatus('live');
    page.stream.send(snapshot([car]));
    await user.click(listed(plate));
    return { ...page, user, car, card: screen.getByRole('region', { name: plate }) };
  }

  it.each(['operator', 'technician', 'admin'] as const)('are open to the %s', async (role) => {
    const user = userEvent.setup();
    const { stream } = renderFleet(role);
    await opened(stream);
    stream.send(snapshot([fleetCar({ plate: '1111 AB-7' })]));
    await user.click(listed('1111 AB-7'));

    const commands = screen.getByRole('group', { name: ru.fleet.commands });
    expect(
      within(commands)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual([
      ru.fleet.command.open,
      ru.fleet.command.close,
      ru.fleet.command.lock_engine,
      ru.fleet.command.unlock_engine,
    ]);
  });

  it('are sent with a comment after the confirmation', async () => {
    const { user, card, car, fleet } = await cardOf('1111 AB-7');

    await user.click(within(card).getByRole('button', { name: ru.fleet.command.lock_engine }));
    const form = screen.getByRole('form', { name: ru.fleet.confirm.title });
    await user.type(within(form).getByLabelText(ru.fleet.confirm.comment), '  Ticket 42  ');
    await user.click(within(form).getByRole('button', { name: ru.fleet.confirm.send }));

    expect(fleet.sendCommand).toHaveBeenCalledWith(car.id, {
      command: 'lock_engine',
      comment: 'Ticket 42',
      confirm_active_rental: false,
    });
    expect(
      await screen.findByText(/Заблокировать двигатель/, { selector: '.fleet-done' }),
    ).toBeDefined();
  });

  it('show that the command runs until the car answers', async () => {
    const { user, card, fleet } = await cardOf('1111 AB-7');
    let answer: () => void = () => undefined;
    fleet.sendCommand.mockImplementationOnce(
      (carId) =>
        new Promise((resolve) => {
          answer = () => {
            resolve({ command_id: 'command-1', car: fleetCar({ id: carId }) });
          };
        }),
    );

    await user.click(within(card).getByRole('button', { name: ru.fleet.command.open }));
    await user.type(screen.getByLabelText(ru.fleet.confirm.comment), 'Cleaning');
    await user.click(screen.getByRole('button', { name: ru.fleet.confirm.send }));

    expect(screen.getByRole('status').textContent).toBe(ru.fleet.confirm.sending);
    expect(
      screen.getByRole('button', { name: ru.fleet.confirm.send }).hasAttribute('disabled'),
    ).toBe(true);
    answer();
    expect(
      await screen.findByText(ru.fleet.result.done.replace('{{command}}', ru.fleet.command.open)),
    ).toBeDefined();
  });

  it('need a comment', async () => {
    const { user, card, fleet } = await cardOf('1111 AB-7');

    await user.click(within(card).getByRole('button', { name: ru.fleet.command.open }));
    await user.type(screen.getByLabelText(ru.fleet.confirm.comment), ' a ');
    await user.click(screen.getByRole('button', { name: ru.fleet.confirm.send }));

    expect(screen.getByRole('alert').textContent).toBe(
      ru.fleet.confirm.problems.short.replace('{{min}}', '3'),
    );
    expect(fleet.sendCommand).not.toHaveBeenCalled();
  });

  it('to a car a client holds need an explicit confirmation', async () => {
    const { user, card, car, fleet } = await cardOf('1111 AB-7', { status: 'in_rental' });

    await user.click(within(card).getByRole('button', { name: ru.fleet.command.close }));
    await user.type(screen.getByLabelText(ru.fleet.confirm.comment), 'Client asked, rental 7');
    await user.click(screen.getByRole('button', { name: ru.fleet.confirm.send }));
    expect(screen.getByRole('alert').textContent).toBe(ru.fleet.confirm.problems.rental);
    expect(fleet.sendCommand).not.toHaveBeenCalled();

    await user.click(screen.getByRole('checkbox', { name: ru.fleet.confirm.rental }));
    await user.click(screen.getByRole('button', { name: ru.fleet.confirm.send }));

    expect(fleet.sendCommand).toHaveBeenCalledWith(car.id, {
      command: 'close',
      comment: 'Client asked, rental 7',
      confirm_active_rental: true,
    });
  });

  it('tell why the car refused', async () => {
    const { user, card, fleet } = await cardOf('1111 AB-7');
    fleet.sendCommand.mockRejectedValueOnce(
      new ApiError(409, 'car-command-rejected', undefined, 'ignition_on'),
    );

    await user.click(within(card).getByRole('button', { name: ru.fleet.command.close }));
    await user.type(screen.getByLabelText(ru.fleet.confirm.comment), 'End of shift');
    await user.click(screen.getByRole('button', { name: ru.fleet.confirm.send }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(ru.fleet.reasons.ignition_on);
    expect(alert.textContent).toContain(ru.fleet.command.close);
  });

  it('warn that a command without an answer may have run', async () => {
    const { user, card, fleet } = await cardOf('1111 AB-7');
    fleet.sendCommand.mockRejectedValueOnce(new ApiError(504, 'telematics-timeout'));

    await user.click(within(card).getByRole('button', { name: ru.fleet.command.open }));
    await user.type(screen.getByLabelText(ru.fleet.confirm.comment), 'Cleaning');
    await user.click(screen.getByRole('button', { name: ru.fleet.confirm.send }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.fleet.commandErrors.timeout);
  });

  it('show the last command of the car by anyone', async () => {
    const { stream, car, card } = await cardOf('1111 AB-7');

    stream.send(commandNotice({ car_id: car.id, command: 'open', outcome: 'rejected' }));

    expect(within(card).getByText(/Последняя команда: «Открыть» — машина отказала/)).toBeDefined();
  });
});
