import { Suspense, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, Outlet, useLocation } from 'react-router';

import { useApis } from '../api/context';
import { useStaff } from '../auth/context';
import { RefreshKycCountContext } from '../kyc/pendingCount';
import { sectionsFor } from '../navigation';
import type { SupportUnread } from '../support/api';
import { menuPollMs, useInterval } from '../support/useInterval';
import { Header } from './Header';

export function AppLayout() {
  const { t } = useTranslation();
  const staff = useStaff();
  const sections = sectionsFor(staff.role);
  const [kycPending, refreshKycCount] = useKycPendingCount(
    sections.some((section) => section.key === 'kyc'),
  );
  const unread = useSupportUnread(sections.some((section) => section.key === 'support'));
  const counts: Partial<Record<(typeof sections)[number]['key'], number>> = unread
    ? { support: unread.conversations, incidents: unread.incidents }
    : {};

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">{t('app.name')}</div>
        <nav aria-label={t('layout.menu')}>
          <ul className="menu">
            {sections.map((section) => (
              <li key={section.key}>
                {/* Only the overview needs an exact match; sections stay active on their sub-pages. */}
                <NavLink to={section.path} end={section.path === '/'} className="menu-link">
                  {t(`nav.${section.key}`)}
                  {section.key === 'kyc' && kycPending ? (
                    <>
                      {/* The space keeps the link's name readable: "Document checks 3". */}{' '}
                      <span
                        className="menu-count"
                        title={t('kyc.menuCount', { count: kycPending })}
                      >
                        {kycPending}
                      </span>
                    </>
                  ) : null}
                  {counts[section.key] ? (
                    <>
                      {' '}
                      <span className="menu-count">{counts[section.key]}</span>
                    </>
                  ) : null}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="main">
        <Header />
        <main className="content">
          <RefreshKycCountContext value={refreshKycCount}>
            {/* Pages with heavy libraries load on first visit (navigation.ts). */}
            <Suspense
              fallback={
                <p role="status" className="muted">
                  {t('layout.pageLoading')}
                </p>
              }
            >
              <Outlet />
            </Suspense>
          </RefreshKycCountContext>
        </main>
      </div>
    </div>
  );
}

/** Unread conversations and new incidents, for the menu; after a failure the last count stays. */
function useSupportUnread(visible: boolean): SupportUnread | null {
  const { support } = useApis();
  const { pathname } = useLocation();
  const [unread, setUnread] = useState<SupportUnread | null>(null);

  const load = useCallback(() => {
    if (!visible) return;
    support.unread().then(setUnread, () => {
      // A page's own requests report the failure; the count only goes stale.
    });
  }, [support, visible]);

  useEffect(load, [load, pathname]);
  useInterval(load, menuPollMs);
  return visible ? unread : null;
}

/**
 * Applications waiting for a decision, for the menu. Asked again on every move between pages,
 * which also covers a decision: the card returns to the queue; the returned function asks again
 * on demand. Null until the first answer; after a failure the last known count stays.
 */
function useKycPendingCount(visible: boolean): [number | null, () => void] {
  const { kycReview } = useApis();
  const { pathname } = useLocation();
  const [count, setCount] = useState<number | null>(null);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => {
    setVersion((previous) => previous + 1);
  }, []);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    kycReview.queue(1).then(
      ({ total }) => {
        if (active) setCount(total);
      },
      () => {
        // A page's own requests report the failure; the count only goes stale.
      },
    );
    return () => {
      active = false;
    };
  }, [kycReview, visible, pathname, version]);

  return [visible ? count : null, refresh];
}
