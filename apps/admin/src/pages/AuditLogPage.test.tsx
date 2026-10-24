import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError } from '../api/client';
import ru from '../i18n/ru.json';
import { auditEvent, fakeAuditLogApi } from '../test/audit';
import { testStaff } from '../test/auth';
import { renderAt } from '../test/render';
import { fakeStaffListApi } from '../test/staff';

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
          target: { type: 'kyc_application', id: '0199b000-1111-7000-8000-000000000000' },
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
    expect(first.textContent).toContain(`${ru.audit.targets.kycApplication} 0199b000`);
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

  it('shows an object type unknown to this build by its code', async () => {
    const auditLog = fakeAuditLogApi({
      items: [
        auditEvent({
          target: { type: 'kyc_document', id: '0199b000-1111-7000-8000-000000000000' },
        }),
      ],
      next_cursor: null,
    });
    renderAt('/audit', testStaff(), { auditLog });

    const row = await findRow();
    expect(row.textContent).toContain('kyc_document 0199b000');
  });

  it('names staff members by email and keeps the id of one the list does not have', async () => {
    const auditLog = fakeAuditLogApi({
      items: [auditEvent({ actor_id: staffId }), auditEvent({ actor_id: otherStaffId })],
      next_cursor: null,
    });
    const staffList = fakeStaffListApi({ id: staffId, email: 'anna@flagman.by', disabled: false });
    renderAt('/audit', testStaff(), { auditLog, staffList });

    const known = await findRow(0);
    await waitFor(() => {
      expect(within(known).getByRole('button').textContent).toBe('anna@flagman.by');
    });
    expect(within(known).getByRole('button').title).toBe(staffId);
    const unknown = await findRow(1);
    expect(within(unknown).getByRole('button').textContent).toBe('0199a7c2');
    expect(within(unknown).getByRole('button').title).toBe(otherStaffId);
    expect(screen.queryByText(ru.audit.staffUnavailable)).toBeNull();
  });

  it('filters by a staff member picked from the list, disabled ones marked', async () => {
    const auditLog = fakeAuditLogApi();
    const staffList = fakeStaffListApi(
      { id: staffId, email: 'anna@flagman.by', disabled: false },
      { id: otherStaffId, email: 'boris@flagman.by', disabled: true },
    );
    renderAt('/audit', testStaff(), { auditLog, staffList });
    await screen.findByText(ru.audit.empty);

    const select = screen.getByLabelText(ru.audit.staff);
    await userEvent.selectOptions(select, 'boris@flagman.by (отключён)');
    await screen.findByText(ru.audit.empty);

    expect(auditLog.list).toHaveBeenLastCalledWith({ actorId: otherStaffId });
    expect(
      within(select)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual([ru.audit.anyStaff, 'anna@flagman.by', 'boris@flagman.by (отключён)']);
  });

  it('keeps a staff member from the address the list does not have', async () => {
    const auditLog = fakeAuditLogApi();
    const staffList = fakeStaffListApi({ id: staffId, email: 'anna@flagman.by', disabled: false });
    renderAt(`/audit?staff=${otherStaffId}`, testStaff(), { auditLog, staffList });
    await screen.findByText(ru.audit.empty);

    const select = screen.getByLabelText<HTMLSelectElement>(ru.audit.staff);
    await waitFor(() => {
      expect(within(select).getAllByRole('option')).toHaveLength(3);
    });
    expect(select.value).toBe(otherStaffId);
    expect(select.selectedOptions[0]?.textContent).toBe('Сотрудник 0199a7c2');
    expect(auditLog.list).toHaveBeenCalledWith({ actorId: otherStaffId });
  });

  it('still shows the log by ids when the staff list does not load', async () => {
    const auditLog = fakeAuditLogApi({
      items: [auditEvent({ actor_id: staffId })],
      next_cursor: null,
    });
    const staffList = fakeStaffListApi();
    staffList.list.mockRejectedValueOnce(new ApiError(500));
    renderAt('/audit', testStaff(), { auditLog, staffList });

    expect(await screen.findByText(ru.audit.staffUnavailable)).toBeDefined();
    const row = await findRow();
    expect(within(row).getByRole('button').textContent).toBe('0199a7c2');
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

    const select = screen.getByLabelText<HTMLSelectElement>(ru.audit.staff);
    await waitFor(() => {
      expect(select.value).toBe(otherStaffId);
    });
    expect(auditLog.list).toHaveBeenLastCalledWith({ actorId: otherStaffId });

    await userEvent.selectOptions(select, ru.audit.anyStaff);
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

  it('offers to reset an address with only malformed filters', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt('/audit?staff=not-a-uuid', testStaff(), { auditLog });
    await screen.findByText(ru.audit.empty);

    await userEvent.click(screen.getByRole('button', { name: ru.audit.reset }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: ru.audit.reset })).toBeNull();
    });
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

  it('does not offer a retry for any other refusal of the request', async () => {
    const auditLog = fakeAuditLogApi();
    auditLog.list.mockRejectedValueOnce(new ApiError(422));
    renderAt('/audit', testStaff(), { auditLog });

    expect((await screen.findByRole('alert')).textContent).toBe(ru.audit.rejected);
    expect(screen.queryByRole('button', { name: ru.audit.retry })).toBeNull();
  });

  it('ignores days before the log began', async () => {
    const auditLog = fakeAuditLogApi();
    renderAt('/audit?from=0001-01-01&to=2025-12-31', testStaff(), { auditLog });
    await screen.findByText(ru.audit.empty);

    expect(auditLog.list).toHaveBeenCalledWith({});
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
    const staffList = fakeStaffListApi();
    renderAt('/audit', testStaff('operator'), { auditLog, staffList });

    expect(await screen.findByRole('heading', { name: ru.forbidden.title })).toBeDefined();
    expect(auditLog.list).not.toHaveBeenCalled();
    expect(staffList.list).not.toHaveBeenCalled();
  });
});
