import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    watch: {
      // Polling burns CPU continuously - one full stat() sweep of the project
      // per interval, forever. It is only needed when the source is bind
      // mounted from a host whose filesystem does not forward inotify events
      // (Docker Desktop on Windows/macOS), so it is opt-in via
      // VITE_USE_POLLING=true rather than always on.
      usePolling: process.env.VITE_USE_POLLING === 'true',
    },
  },
  build: {
    // Source maps roughly double build memory and output size; enable
    // explicitly when debugging a production build.
    sourcemap: false,
    target: 'es2020',
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // Keep the big, rarely-changing dependencies in their own chunks so a
        // change to application code does not invalidate them in browser
        // caches, and so charts/diff code is not part of the initial payload.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          diff: ['react-diff-viewer-continued'],
          query: ['@tanstack/react-query', 'axios'],
        },
      },
    },
  },
})
