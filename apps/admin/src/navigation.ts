import { lazy, type ComponentType } from 'react';

import type { StaffRole } from './auth/staff';
import type ru from './i18n/ru.json';
import { AuditLogPage } from './pages/AuditLogPage';
import { KycApplicationPage } from './pages/KycApplicationPage';
import { KycQueuePage } from './pages/KycQueuePage';
import { TariffsPage } from './pages/TariffsPage';

// The map library is large: it loads with the map pages, not with the sign-in page.
const ZonesPage = lazy(() =>
  import('./pages/ZonesPage').then(({ ZonesPage }) => ({ default: ZonesPage })),
);
const FleetPage = lazy(() =>
  import('./pages/FleetPage').then(({ FleetPage }) => ({ default: FleetPage })),
);

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
  { key: 'fleet', path: '/fleet', roles: everyone, page: FleetPage },
  { key: 'rentals', path: '/rentals', roles: support },
  { key: 'clients', path: '/clients', roles: support },
  // Everyone sees the zones; only an admin changes them, inside the page and in the API.
  { key: 'zones', path: '/zones', roles: everyone, page: ZonesPage },
  // Operators see the tariffs to answer clients; only an admin changes them, here and in the API.
  { key: 'tariffs', path: '/tariffs', roles: support, page: TariffsPage },
  { key: 'audit', path: '/audit', roles: adminOnly, page: AuditLogPage },
];

export function sectionsFor(role: StaffRole): Section[] {
  return sections.filter((section) => section.roles.includes(role));
}
