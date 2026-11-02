import { RentalStore, stageOf } from './rentalStore';
import { fakeRentalApi, inspection, nearCar, ok, paidRental, rental, serverNow } from './testing';

const trip = rental({
  status: 'in_trip',
  inspection_submitted: true,
  trip_started_at: '2026-10-26T11:50:00Z',
});

function setup() {
  const fake = fakeRentalApi();
  const store = new RentalStore(
    fake.api,
    () => Promise.resolve({ kind: 'located', location: nearCar }),
    () => Date.parse(serverNow),
  );
  return { ...fake, store };
}

describe('RentalStore during the trip', () => {
  it('takes the inspection before the trip at the reservation and after it on the road', () => {
    expect(stageOf(rental())).toBe('before');
    expect(stageOf(trip)).toBe('after');
    expect(stageOf({ ...trip, status: 'parked' })).toBe('after');
  });

  it('comes back to the trip after a restart, with the debts', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValue(ok(trip));
    api.debts.mockResolvedValue(ok([{ rental_id: 'old', amount: 300, ended_at: serverNow }]));

    await store.restore();

    expect(store.getState().rental?.status).toBe('in_trip');
    expect(store.getState().debts).toEqual([
      { rental_id: 'old', amount: 300, ended_at: serverNow },
    ]);
  });

  it('inspects the car after the trip and marks that inspection done', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValue(ok(trip));
    await store.restore();
    const after = inspection(4, { stage: 'after' });
    api.inspection.mockResolvedValue(ok(after));
    api.submitInspection.mockResolvedValue(ok({ ...after, submitted_at: serverNow }));

    await store.loadInspection();
    await store.submitInspection();

    expect(api.inspection).toHaveBeenCalledWith('rental-1', 'after');
    expect(api.submitInspection).toHaveBeenCalledWith('rental-1', 'after');
    expect(store.getState().rental).toMatchObject({
      inspection_submitted: true,
      after_inspection_submitted: true,
    });
  });

  it('parks, finishes and keeps the ended rental with its receipt', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValue(ok(trip));
    await store.restore();
    api.park.mockResolvedValue(ok({ ...trip, status: 'parked' }));
    api.finish.mockResolvedValue(ok(paidRental()));

    await store.park();
    expect(store.getState().rental?.status).toBe('parked');
    expect(await store.finish()).toBeNull();

    expect(store.getState().rental?.receipt?.cost.total).toBe(864);
    expect(api.debts).toHaveBeenCalledTimes(2);
  });

  it('keeps the trip when a check fails, with the reason', async () => {
    const { api, store } = setup();
    api.current.mockResolvedValue(ok(trip));
    await store.restore();
    api.finish.mockResolvedValue({ ok: false, error: { kind: 'outsideZone' } });

    expect(await store.finish()).toEqual({ kind: 'outsideZone' });

    expect(store.getState().rental?.status).toBe('in_trip');
    expect(store.getState().error).toEqual({ kind: 'outsideZone' });
  });

  it('opens a rental by its id and pays its debt', async () => {
    const { api, store } = setup();
    const failed = paidRental({ status: 'payment_failed', charge_status: 'failed' });
    api.get.mockResolvedValue(ok(failed));
    api.payDebt.mockResolvedValue(ok(paidRental()));

    await store.open('rental-1');
    await store.payDebt();

    expect(api.get).toHaveBeenCalledWith('rental-1');
    expect(store.getState().rental?.status).toBe('paid');
    expect(api.debts).toHaveBeenCalled();
  });
});
