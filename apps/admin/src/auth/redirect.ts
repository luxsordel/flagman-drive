import type { Location } from 'react-router';

/** Navigation state of `/login`: where to go once signed in. */
interface LoginRedirect {
  from: string;
}

/** The state `/login` gets from a page that needs a session, to come back to it afterwards. */
export function loginRedirect(location: Pick<Location, 'pathname' | 'search'>): LoginRedirect {
  return { from: location.pathname + location.search };
}

/** Only paths inside the panel: history state is not trusted, any script of the page can set it. */
export function redirectTarget(state: unknown): string {
  const from = (state as Partial<LoginRedirect> | null)?.from;
  if (typeof from !== 'string' || !from.startsWith('/')) return '/';
  // Ask the URL parser instead of guessing its rules: it reads "/\host" as "//host" and drops
  // tabs and line breaks, so "/\t/host" leads to another site too.
  const { origin } = window.location;
  return new URL(from, origin).origin === origin ? from : '/';
}
