import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The built dashboard is served by the API from sentinel/static, one origin, no CORS.
// In development, Vite proxies the API and MCP paths to a running `system-sentinel serve`.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../sentinel/static', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/mcp': 'http://127.0.0.1:8000',
    },
  },
});
