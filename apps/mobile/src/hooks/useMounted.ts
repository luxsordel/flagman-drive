import { useEffect, useRef, type RefObject } from 'react';

/** False once the screen is gone: a request it started may answer after the client left. */
export function useMounted(): RefObject<boolean> {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return mounted;
}
