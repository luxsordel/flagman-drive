// Copies the MapLibre worker to public/, where Expo serves it for the web map (mapWorker.ts).
// Metro bundles the app into one file and does not emit the worker on its own. A generated copy
// rather than a committed one: it always matches the installed maplibre-gl.
import { copyFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const source = join(
  dirname(require.resolve('maplibre-gl/package.json')),
  'dist',
  'maplibre-gl-worker.mjs',
);
const publicDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');

mkdirSync(publicDir, { recursive: true });
copyFileSync(source, join(publicDir, 'maplibre-gl-worker.mjs'));
