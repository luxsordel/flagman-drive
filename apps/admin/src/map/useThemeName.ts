import type { ThemeName } from '@flagman/design-tokens';
import { useEffect, useState } from 'react';

import { currentTheme } from '../theme';

/** The theme the panel shows now; maps take their colors from it, as CSS cannot reach them. */
export function useThemeName(): ThemeName {
  const [theme, setTheme] = useState(currentTheme);
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(currentTheme());
    });
    observer.observe(document.documentElement, { attributeFilter: ['data-theme'] });
    return () => {
      observer.disconnect();
    };
  }, []);
  return theme;
}
