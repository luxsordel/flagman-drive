import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import { auditEvent, fakeAuditLogApi } from '../test/audit';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';

const staffId = '0199a7c2-5d1e-7b3a-9f00-000000000001';
const otherStaffId = '0199a7c2-5d1e-7b3a-9f00-000000000002';

async function findRows(): Promise<HTMLElement[]> {
  const table = await screen.findByRole('table');
  return within(table).getAllByRole('row').slice(1);
}

async function findRow(index = 0): Promise<HTMLElement> {
  const row = (await findRows())[index];
  if (!row) throw new Error(`there is no row ${String(index)}`);
  return row;
}

describe('audit log page', () => {
  it('shows the events in Minsk time with who did what to which object', async () => {
    const auditLog = fakeAuditLogApi({
      items: [
        auditEvent({
          occurred_at: '2026-10-23T12:04:05Z',
          actor_id: staffId,
          action: 'staff.sign_in_failed',
          target: { type: 'kyc_document', id: '0199b000-1111-7000-8000-000000000000' },
        }),
        auditEvent({ actor_type: 'system', actor_id: null }),
      ],
      next_cursor: null,
    });
    renderAt('/audit', testStaff(), { auditLog });

    const first = await findRow(0);
    const second = await findRow(1);
    expect(first.textContent).toContain('23.10.2026, 15:04:05');
    expect(within(first).getByRole('button', { name: /0199a7c2/ }).title).toBe(staffId);
    expect(first.textContent).toContain(ru.audit.actions.staffSignInFailed);
    expect(first.textContent).toContain('kyc_document 0199b000');
    expect(second.textContent).toContain(ru.audit.actor.system);
    expect(second.textContent).toContain(ru.audit.noTarget);
    expect(auditLog.list).toHaveBeenCalledWith({});
  });

  it('shows an action unknown to this build by its code', async () => {
    const auditLog = fakeAuditLogApi({
      items: [auditEvent({ action: 'rental.force_finished' })],
      next_cursor: null,
    });
    renderAt('/audit', testStaff(), { auditLog });

    const row = await findRow();
    expect(row.textContent).toContain('rental.force_finished');
  });

  it('filters by action and period', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt('/audit', testStaff(), { auditLog });
    await screen.findByText(ru.audit.empty);

    await userEvent.selectOptions(
      screen.getByLabelText(ru.audit.action),
      ru.audit.actions.staffSignedIn,
    );
    fireEvent.change(screen.getByLabelText(ru.audit.from), { target: { value: '2026-10-01' } });
    fireEvent.change(screen.getByLabelText(ru.audit.to), { target: { value: '2026-10-23' } });
    await screen.findByText(ru.audit.empty);

    expect(auditLog.list).toHaveBeenLastCalledWith({
      action: 'staff.signed_in',
      from: '2026-10-01',
      to: '2026-10-23',
    });
  });

  it('takes the filter from the address and drops malformed values', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt(
      '/audit?action=staff.signed_in&staff=not-a-uuid&from=yesterday&to=2026-02-30',
      testStaff(),
      { auditLog },
    );
    await screen.findByText(ru.audit.empty);

    expect(auditLog.list).toHaveBeenCalledWith({ action: 'staff.signed_in' });
  });

  it('filters by the staff member clicked in a row and clears that filter', async () => {
    const event = auditEvent({ actor_id: otherStaffId });
    const auditLog = fakeAuditLogApi({ items: [event], next_cursor: null });
    renderAt('/audit', testStaff(), { auditLog });

    const row = await findRow();
    await userEvent.click(within(row).getByRole('button'));

    expect(await screen.findByText('Сотрудник 0199a7c2')).toBeDefined();
    expect(auditLog.list).toHaveBeenLastCalledWith({ actorId: otherStaffId });

    await userEvent.click(screen.getByRole('button', { name: ru.audit.clearStaff }));
    await screen.findByText(ru.audit.empty);
    expect(auditLog.list).toHaveBeenLastCalledWith({});
  });

  it('resets every filter at once', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt(`/audit?action=staff.signed_in&staff=${staffId}`, testStaff(), { auditLog });
    await screen.findByText(ru.audit.empty);

    await userEvent.click(screen.getByRole('button', { name: ru.audit.reset }));
    await screen.findByText(ru.audit.empty);

    expect(auditLog.list).toHaveBeenLastCalledWith({});
    expect(screen.queryByRole('button', { name: ru.audit.reset })).toBeNull();
  });

  it('loads the next page by the cursor and stops on the last one', async () => {
    const auditLog = fakeAuditLogApi(
      { items: [auditEvent()], next_cursor: 'cursor-2' },
      { items: [auditEvent()], next_cursor: null },
    );
    renderAt('/audit', testStaff(), { auditLog });
    expect(await findRows()).toHaveLength(1);

    await userEvent.click(screen.getByRole('button', { name: ru.audit.loadMore }));

    await waitFor(() => {
      expect(screen.getAllByRole('row')).toHaveLength(3);
    });
    expect(auditLog.list).toHaveBeenLastCalledWith({}, 'cursor-2');
    expect(screen.queryByRole('button', { name: ru.audit.loadMore })).toBeNull();
  });

  it('keeps the loaded rows when the next page fails and lets retry it', async () => {
    const auditLog = fakeAuditLogApi({ items: [auditEvent()], next_cursor: 'cursor-2' });
    auditLog.list.mockRejectedValueOnce(new Error('network'));
    renderAt('/audit', testStaff(), { auditLog });
    await findRows();

    await userEvent.click(screen.getByRole('button', { name: ru.audit.loadMore }));

    expect((await screen.findByRole('alert')).textContent).toBe(ru.audit.moreFailed);
    expect(await findRows()).toHaveLength(1);
    expect(screen.getByRole('button', { name: ru.audit.loadMore })).toBeDefined();
  });

  it('offers to retry when the log does not load', async () => {
    const auditLog = fakeAuditLogApi({ items: [auditEvent()], next_cursor: null });
    auditLog.list.mockReset();
    auditLog.list
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce({ items: [auditEvent()], next_cursor: null });
    renderAt('/audit', testStaff(), { auditLog });

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(ru.audit.failed);
    await userEvent.click(within(alert).getByRole('button', { name: ru.audit.retry }));

    expect(await findRows()).toHaveLength(1);
  });

  it('asks to swap the dates instead of loading an inverted period', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt('/audit?from=2026-10-23&to=2026-10-01', testStaff(), { auditLog });

    expect((await screen.findByRole('alert')).textContent).toBe(ru.audit.invertedRange);
    expect(auditLog.list).not.toHaveBeenCalled();
  });

  it('says there is no access instead of offering a retry when the API refuses', async () => {
    const auditLog = fakeAuditLogApi();
    auditLog.list.mockRejectedValueOnce(new ApiError(403));
    renderAt('/audit', testStaff(), { auditLog });

    expect((await screen.findByRole('alert')).textContent).toBe(ru.forbidden.text);
    expect(screen.queryByRole('button', { name: ru.audit.retry })).toBeNull();
  });

  it('sends to the sign-in page when the API no longer accepts the session', async () => {
    const auditLog = fakeAuditLogApi();
    auditLog.list.mockRejectedValueOnce(new ApiError(401));
    const { api } = renderAt('/audit', testStaff(), { auditLog });

    expect(await screen.findByRole('heading', { name: ru.login.title })).toBeDefined();
    expect(api.logout).toHaveBeenCalled();
  });

  it('is closed to other roles without asking the API', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt('/audit', testStaff('operator'), { auditLog });

    expect(await screen.findByRole('heading', { name: ru.forbidden.title })).toBeDefined();
    expect(auditLog.list).not.toHaveBeenCalled();
  });
});
