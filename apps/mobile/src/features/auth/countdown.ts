import { useEffect, useState } from 'react';

/** Seconds as `m:ss`, the same in every language, so no plural forms are needed. */
export function formatWait(seconds: number): string {
  const whole = Math.max(0, Math.ceil(seconds));
  return `${String(Math.floor(whole / 60))}:${String(whole % 60).padStart(2, '0')}`;
}

/**
 * Whole seconds left until `deadline` (epoch ms), ticking once a second. The clock is read on
 * mount, so a component with a new deadline is remounted with `key={deadline}`.
 */
export function useSecondsUntil(deadline: number): number {
  const [now, setNow] = useState(Date.now);
  const left = Math.max(0, Math.ceil((deadline - now) / 1000));
  const running = left > 0;

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [running]);

  return left;
}
