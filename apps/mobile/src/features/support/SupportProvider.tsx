import { createApiClient } from '@flagman/api-client';
import { randomUUID } from 'expo-crypto';
import { createContext, useContext, useEffect, useSyncExternalStore, type ReactNode } from 'react';
import { AppState } from 'react-native';

import { appApiUrl } from '../auth/session/AuthProvider';
import type { AuthSession } from '../auth/session/authSession';
import { ChatStore, type ChatState } from './chatStore';
import { createSupportApi, type SupportApi } from './supportApi';

/** How often waiting messages are tried again, on any screen of the app. */
const resendEveryMs = 4000;

export interface SupportDeps {
  api: SupportApi;
  chat: ChatStore;
  newId: () => string;
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
  return { api, chat, newId: randomUUID };
}

const SupportContext = createContext<SupportDeps | null>(null);

export function SupportProvider({ deps, children }: { deps: SupportDeps; children: ReactNode }) {
  return (
    <SupportContext value={deps}>
      <ResendWaiting chat={deps.chat} />
      {children}
    </SupportContext>
  );
}

/** Not the chat's poll: that one marks answers as read, so it runs only while they are on screen. */
function ResendWaiting({ chat }: { chat: ChatStore }) {
  const waiting = useSyncExternalStore(chat.subscribe, () =>
    chat.getState().outgoing.some((item) => item.state === 'waiting'),
  );

  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(() => {
      if (AppState.currentState === 'active') void chat.resend();
    }, resendEveryMs);
    return () => {
      clearInterval(timer);
    };
  }, [chat, waiting]);

  return null;
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
