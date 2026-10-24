import type { ComponentType } from 'react';

import type { StaffRole } from './auth/staff';
import type ru from './i18n/ru.json';
import { AuditLogPage } from './pages/AuditLogPage';
import { KycApplicationPage } from './pages/KycApplicationPage';
import { KycQueuePage } from './pages/KycQueuePage';

export type SectionKey = keyof typeof ru.nav;

export interface Section {
  key: SectionKey;
  path: string;
  /** Who sees the section; the API enforces the same roles on its endpoints. */
  roles: readonly StaffRole[];
  /** Without one the section shows the placeholder. */
  page?: ComponentType;
  /** Pages inside the section, under the same roles; paths are relative to the section's. */
  subpages?: readonly { path: string; page: ComponentType }[];
}

const everyone: readonly StaffRole[] = ['operator', 'technician', 'admin'];
const support: readonly StaffRole[] = ['operator', 'admin'];
const adminOnly: readonly StaffRole[] = ['admin'];

/** Admin sections and roles from docs/01-vision-and-mvp.md, in menu order. */
export const sections: readonly Section[] = [
  { key: 'dashboard', path: '/', roles: everyone },
  {
    key: 'kyc',
    path: '/kyc',
    roles: support,
    page: KycQueuePage,
    subpages: [{ path: ':applicationId', page: KycApplicationPage }],
  },
  { key: 'fleet', path: '/fleet', roles: everyone },
  { key: 'rentals', path: '/rentals', roles: support },
  { key: 'clients', path: '/clients', roles: support },
  { key: 'zones', path: '/zones', roles: adminOnly },
  { key: 'tariffs', path: '/tariffs', roles: adminOnly },
  { key: 'audit', path: '/audit', roles: adminOnly, page: AuditLogPage },
];

export function sectionsFor(role: StaffRole): Section[] {
  return sections.filter((section) => section.roles.includes(role));
}
