import type ru from './i18n/ru.json';

export type SectionKey = keyof typeof ru.nav;

export interface Section {
  key: SectionKey;
  path: string;
}

/** Admin sections from docs/01-vision-and-mvp.md, in menu order. */
export const sections: readonly Section[] = [
  { key: 'dashboard', path: '/' },
  { key: 'kyc', path: '/kyc' },
  { key: 'fleet', path: '/fleet' },
  { key: 'rentals', path: '/rentals' },
  { key: 'clients', path: '/clients' },
  { key: 'zones', path: '/zones' },
  { key: 'tariffs', path: '/tariffs' },
  { key: 'audit', path: '/audit' },
];
