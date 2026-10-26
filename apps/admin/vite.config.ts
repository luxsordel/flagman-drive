import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The API from infra/docker-compose.yml. Proxied rather than called directly, so the panel and the
// API share one origin: no CORS with credentials, and the refresh cookie belongs to the panel's host.
// Paths are kept as is, so the cookie's Path=/staff/auth still matches.
const api = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  // MapLibre starts its worker as an ES module (src/map/worker.ts).
  worker: { format: 'es' },
  // Bound to localhost only, like the rest of the local stack (infra/docker-compose.yml).
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // ws: the live fleet comes over a WebSocket at /admin/fleet/stream (docs/adr/0017).
    proxy: { '^/(staff|admin)/': { target: api, ws: true } },
  },
});
