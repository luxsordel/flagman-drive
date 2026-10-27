import type { PushListenersProps } from './NotificationsContext';

/** The web gets no pushes, and expo-notifications has no listeners there (ADR-0020). */
export const PushListeners: (props: PushListenersProps) => null = () => null;
