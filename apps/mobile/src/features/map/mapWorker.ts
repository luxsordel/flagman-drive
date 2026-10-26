import { setWorkerUrl } from 'maplibre-gl';

// MapLibre loads its worker from a file next to its own, which Metro does not emit: the map then
// stays blank. The worker is copied to public/ instead (scripts/copy-map-worker.mjs), and Expo
// serves public/ at the root of the site.
setWorkerUrl('/maplibre-gl-worker.mjs');
