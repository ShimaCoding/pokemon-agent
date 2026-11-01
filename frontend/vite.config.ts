import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { rmSync } from 'node:fs'
import { resolve } from 'node:path'

// Backend the dev server proxies to. Conductor workspaces set BACKEND_URL to their
// own backend port; `make frontend` keeps using the default.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    {
      name: 'clean-assets',
      // Only on `vite build`: in dev, buildStart also fires and would delete the
      // committed bundle in backend/static/assets.
      apply: 'build',
      buildStart() {
        // Remove only the assets subfolder so stale hashed bundles don't pile up,
        // while keeping other static files (robots.txt, fonts, og-image, etc.).
        rmSync(resolve(__dirname, '../backend/static/assets'), { recursive: true, force: true })
      },
    },
  ],
  build: {
    outDir: '../backend/static',
    emptyOutDir: false,
  },
  server: {
    proxy: {
      '/api': backendUrl,
      '/providers': backendUrl,
      '/config': backendUrl,
      '/health': backendUrl,
    },
  },
})
