import { setWorkerUrl } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

// MapLibre looks for its worker next to its own file, which Vite neither pre-bundles nor copies
// to the build: the map then stays blank. Vite bundles the worker here and hands its address over.
setWorkerUrl(workerUrl);
