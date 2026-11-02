import { useEffect, useRef } from 'react';

/** Open chat (ADR-0023): often enough to read as live, rare enough for a whole shift of operators. */
export const chatPollMs = 4_000;
export const listPollMs = 15_000;
export const menuPollMs = 30_000;

/** Calls `tick` every `ms` while the tab is visible: a hidden tab has nobody to show news to. */
export function useInterval(tick: () => void, ms: number): void {
  const latest = useRef(tick);
  useEffect(() => {
    latest.current = tick;
  });

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') latest.current();
    }, ms);
    return () => {
      clearInterval(timer);
    };
  }, [ms]);
}
