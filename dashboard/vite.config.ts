import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The built dashboard is served by the API from sentinel/static, one origin, no CORS.
// In development, Vite proxies the API and MCP paths to a running `system-sentinel serve`
// (port 8000, or SENTINEL_PORT).
const api = `http://127.0.0.1:${process.env.SENTINEL_PORT ?? '8000'}`;

export default defineConfig({
  plugins: [react()],
  build: { outDir: '../sentinel/static', emptyOutDir: true },
  server: {
    proxy: {
      '/api': api,
      '/mcp': api,
    },
  },
});
