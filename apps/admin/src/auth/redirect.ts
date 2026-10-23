import type { Location } from 'react-router';

/** Navigation state of `/login`: where to go once signed in. */
interface LoginRedirect {
  from: string;
}

/** The state `/login` gets from a page that needs a session, to come back to it afterwards. */
export function loginRedirect(location: Pick<Location, 'pathname' | 'search'>): LoginRedirect {
  return { from: location.pathname + location.search };
}

// One "/" and then not another "/" or "\": URL parsing reads "/\host" as "//host", another site.
const panelPath = /^\/(?![/\\])/;

/** Only paths inside the panel: history state is not trusted, any script of the page can set it. */
export function redirectTarget(state: unknown): string {
  const from = (state as Partial<LoginRedirect> | null)?.from;
  return typeof from === 'string' && panelPath.test(from) ? from : '/';
}
