import { useTranslation } from 'react-i18next';
import { NavLink, Outlet } from 'react-router';

import { useStaff } from '../auth/context';
import { sectionsFor } from '../navigation';
import { Header } from './Header';

export function AppLayout() {
  const { t } = useTranslation();
  const staff = useStaff();

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">{t('app.name')}</div>
        <nav aria-label={t('layout.menu')}>
          <ul className="menu">
            {sectionsFor(staff.role).map((section) => (
              <li key={section.key}>
                {/* Only the overview needs an exact match; sections stay active on their sub-pages. */}
                <NavLink to={section.path} end={section.path === '/'} className="menu-link">
                  {t(`nav.${section.key}`)}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="main">
        <Header />
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
