/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/portfolio_agent/web/static', emptyOutDir: true, sourcemap: false },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'] },
});
