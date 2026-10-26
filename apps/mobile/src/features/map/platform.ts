import Constants, { ExecutionEnvironment } from 'expo-constants';

/**
 * Expo Go has no MapLibre native module (ADR-0016): the map must not even be imported there,
 * or the app crashes at start. Everything else of the app still runs in Expo Go.
 */
export const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
