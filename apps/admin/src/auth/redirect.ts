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
  // The URL parser drops tabs and line breaks and reads "\" as "/", and the router resolves
  // paths by its own rules: with none of these characters both read `from` the same way.
  if (typeof from !== 'string' || !from.startsWith('/') || /[\t\n\r\\]/.test(from)) return '/';
  const { origin } = window.location;
  let target: URL;
  try {
    target = new URL(from, origin);
  } catch {
    // "//x:y" or "//[" is a broken host, not a path: it must not break the sign-in page.
    return '/';
  }
  return target.origin === origin ? from : '/';
}
