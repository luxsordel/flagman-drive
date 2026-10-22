import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // Bound to localhost only, like the rest of the local stack (infra/docker-compose.yml).
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
});
