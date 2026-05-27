/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // In development, forward API requests to the FastAPI server so the
    // browser thinks it's calling its own origin (avoids CORS issues).
    // The frontend code calls e.g. fetch('/drifts'), Vite forwards it to
    // http://localhost:8000/drifts.
    proxy: {
      '/drifts': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
  test: {
    // Vitest config. `environment: 'jsdom'` gives tests a fake browser DOM
    // so components can be rendered without opening a real browser.
    // `globals: true` makes describe/it/expect available without imports,
    // matching the style of pytest fixtures-on-demand. `setupFiles` runs
    // once before every test file (we use it to register the jest-dom
    // matchers, which give us toBeInTheDocument(), etc.).
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
  },
})