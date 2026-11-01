import { createApiClient } from '@flagman/api-client';
import { randomUUID } from 'expo-crypto';
import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import { ChatStore, type ChatState } from './chatStore';
import { createSupportApi, type SupportApi } from './supportApi';

export interface SupportDeps {
  api: SupportApi;
  chat: ChatStore;
}

export function createAppSupport(session: AuthSession): SupportDeps {
  const baseUrl = appApiUrl();
  const api = createSupportApi({
    api: createApiClient({ baseUrl, getAccessToken: session.getAccessToken }),
    baseUrl,
    getAccessToken: session.getAccessToken,
  });
  const chat = new ChatStore(api, randomUUID);
  session.subscribe(() => {
    if (session.getStatus() === 'signedOut') chat.reset();
  });
  return { api, chat };
}

const SupportContext = createContext<SupportDeps | null>(null);

export function SupportProvider({ deps, children }: { deps: SupportDeps; children: ReactNode }) {
  return <SupportContext value={deps}>{children}</SupportContext>;
}

export function useSupport(): SupportDeps {
  const deps = useContext(SupportContext);
  if (deps === null) throw new Error('useSupport must be used inside SupportProvider');
  return deps;
}

export function useChatState(): ChatState {
  const { chat } = useSupport();
  return useSyncExternalStore(chat.subscribe, chat.getState);
}
