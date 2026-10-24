import { createContext, useContext } from 'react';

/**
 * Asks the menu to count the waiting applications again. Moving between pages does that by itself;
 * this is for a change the address does not show, such as learning another operator decided one.
 */
export const RefreshKycCountContext = createContext<() => void>(() => undefined);

export function useRefreshKycCount(): () => void {
  return useContext(RefreshKycCountContext);
}
