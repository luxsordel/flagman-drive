import { lazy } from 'react';

/**
 * The map, loaded only when rendered: in Expo Go the native map module is missing and must not
 * even be imported (ADR-0016). On the web this also keeps maplibre-gl out of the first bundle.
 */
export const LazyCarMap = lazy(() => import('./CarMap'));
