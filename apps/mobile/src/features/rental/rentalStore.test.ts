import type { LocationFix } from '../map/location';
import { RentalStore } from './rentalStore';
import { fakeRentalApi, inspection, nearCar, ok, rental, serverNow } from './testing';

const phoneNow = Date.parse(serverNow) - 3000;

function setup(fix: LocationFix = { kind: 'located', location: nearCar }) {
  const fake = fakeRentalApi();
  const locate = jest.fn(() => Promise.resolve(fix));
  const store = new RentalStore(fake.api, locate, () => phoneNow);
  return { ...fake, locate, store };
}

describe('RentalStore', () => {
  it('restores the active rental and the server clock', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValue(ok(rental()));

    await store.restore();

    expect(store.getState()).toMatchObject({ restored: true, rental: { id: 'rental-1' } });
    expect(store.getState().offsetMs).toBe(3000);
  });

  it('keeps the map working when the restore fails', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValue({ ok: false, error: { kind: 'network' } });

    await store.restore();

    expect(store.getState()).toMatchObject({ restored: true, rental: null });
    expect(store.getState().error).toEqual({ kind: 'network' });
  });

  it('keeps an ended rental on screen until dismissed', async () => {
    const { api, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');
    api.cancel.mockResolvedValue(ok(rental({ status: 'cancelled', cancel_reason: 'client' })));
    await store.cancel();

    await store.restore();
    expect(store.getState().rental?.status).toBe('cancelled');

    store.dismiss();
    expect(store.getState().rental).toBeNull();
  });

  it('never dismisses an active rental', async () => {
    const { api, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');

    store.dismiss();

    expect(store.getState().rental).not.toBeNull();
  });

  it('reloads the rental when the reservation expired meanwhile', async () => {
    const { api, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');
    api.submitInspection.mockResolvedValue({ ok: false, error: { kind: 'expired' } });
    api.get.mockResolvedValue(ok(rental({ status: 'cancelled', cancel_reason: 'expired' })));

    const error = await store.submitInspection();

    expect(error).toEqual({ kind: 'expired' });
    expect(store.getState().rental?.cancel_reason).toBe('expired');
  });

  it('sends a command with the fresh location and starts the trip', async () => {
    const { api, locate, store } = setup();
    api.reserve.mockResolvedValue(ok(rental({ inspection_submitted: true })));
    await store.reserve('car-1');
    api.command.mockResolvedValue(ok(rental({ status: 'in_trip', inspection_submitted: true })));

    const error = await store.command('open');

    expect(error).toBeNull();
    expect(locate).toHaveBeenCalledTimes(1);
    expect(api.command).toHaveBeenCalledWith('rental-1', 'open', nearCar);
    expect(store.getState().rental?.status).toBe('in_trip');
  });

  it('explains a refused location without asking the server', async () => {
    const { api, store } = setup({ kind: 'denied' });
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');

    const error = await store.command('open');

    expect(error).toEqual({ kind: 'locationDenied' });
    expect(api.command).not.toHaveBeenCalled();
    expect(store.getState().pending).toBeNull();
  });

  it('marks the rental inspected once the inspection is submitted', async () => {
    const { api, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');
    api.submitInspection.mockResolvedValue(ok(inspection(4, { submitted_at: serverNow })));

    await store.submitInspection();

    expect(store.getState().rental?.inspection_submitted).toBe(true);
    expect(store.getState().inspection?.submitted_at).toBe(serverNow);
  });

  it('takes one action at a time', async () => {
    const { api, store } = setup();
    let finish: (value: ReturnType<typeof ok<ReturnType<typeof rental>>>) => void = () => undefined;
    api.reserve.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );

    const first = store.reserve('car-1');
    const second = await store.reserve('car-2');
    finish(ok(rental()));
    await first;

    expect(second).toBeNull();
    expect(api.reserve).toHaveBeenCalledTimes(1);
  });

  it('forgets the rental at sign-out, also an answer that comes after', async () => {
    const { api, store } = setup();
    let finish: (value: ReturnType<typeof ok<ReturnType<typeof rental> | null>>) => void = () =>
      undefined;
    api.current.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );

    const restoring = store.restore();
    store.reset();
    finish(ok(rental()));
    await restoring;

    expect(store.getState()).toMatchObject({ restored: false, rental: null });
  });

  it('keeps the uploaded photos of the inspection', async () => {
    const { api, uploads, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');

    const upload = store.uploadPhoto('file:///photo.jpg');
    uploads[0]?.finish(ok(inspection(1)));
    await upload.done;

    expect(store.getState().inspection?.photos).toHaveLength(1);
  });

  it('tells how a reservation ended while the app was in the background', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValueOnce(ok(rental()));
    await store.restore();
    api.current.mockResolvedValueOnce(ok(null));
    api.get.mockResolvedValue(ok(rental({ status: 'cancelled', cancel_reason: 'expired' })));

    await store.restore();

    expect(api.get).toHaveBeenCalledWith('rental-1');
    expect(store.getState().rental?.cancel_reason).toBe('expired');
  });

  it('blames the location, not the network, when there is no fix', async () => {
    const { api, store } = setup({ kind: 'unavailable' });
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');

    expect(await store.command('open')).toEqual({ kind: 'locationUnavailable' });
    expect(api.command).not.toHaveBeenCalled();
  });

  it('opens an earlier rental beside the active one, not instead of it', async () => {
    const { api, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');
    const earlier = rental({ id: 'rental-0', status: 'payment_failed', charge_status: 'failed' });
    api.get.mockResolvedValue(ok(earlier));

    await store.open('rental-0');

    expect(store.getState().rental?.id).toBe('rental-1');
    expect(store.getState().viewed?.id).toBe('rental-0');
    api.payDebt.mockResolvedValue(ok({ ...earlier, status: 'paid', charge_status: 'paid' }));
    await store.payDebt();
    expect(api.payDebt).toHaveBeenCalledWith('rental-0');
    expect(store.getState().viewed?.status).toBe('paid');

    store.dismiss();
    expect(store.getState()).toMatchObject({ rental: { id: 'rental-1' }, viewed: null });
  });

  it('reloads the debts after a cancel: a declined paid wait is one', async () => {
    const { api, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');
    api.cancel.mockResolvedValue(ok(rental({ status: 'cancelled', charge_status: 'failed' })));
    const debt = { rental_id: 'rental-1', amount: 500, ended_at: serverNow };
    api.debts.mockResolvedValue(ok([debt]));

    await store.cancel();

    expect(store.getState().debts).toEqual([debt]);
  });

  it('counts the photos on their way', async () => {
    const { api, uploads, store } = setup();
    api.reserve.mockResolvedValue(ok(rental()));
    await store.reserve('car-1');

    const upload = store.uploadPhoto('file:///photo.jpg');
    expect(store.getState().uploading).toBe(1);
    uploads[0]?.finish({ ok: false, error: { kind: 'network' } });
    await upload.done;

    expect(store.getState().uploading).toBe(0);
  });
});
