import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { rmSync } from 'node:fs'
import { resolve } from 'node:path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    {
      name: 'clean-assets',
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
      '/api': 'http://localhost:8000',
      '/providers': 'http://localhost:8000',
      '/config': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
