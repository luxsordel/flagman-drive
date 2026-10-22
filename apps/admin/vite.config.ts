import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The API from infra/docker-compose.yml. Proxied rather than called directly: 127.0.0.1:5173 and
// localhost:8000 are different sites, and the SameSite=Strict refresh cookie only travels same-site.
// Paths are kept as is, so the cookie's Path=/staff/auth still matches.
const api = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  // Bound to localhost only, like the rest of the local stack (infra/docker-compose.yml).
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '^/(staff|admin)/': api },
  },
});
