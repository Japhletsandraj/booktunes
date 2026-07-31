import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API's CORS allowlist is explicit (never "*", because credentials are
// allowed), so the dev server proxies /api to the backend instead. That keeps
// the browser origin identical to the page origin and sidesteps CORS entirely
// during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind IPv4 explicitly. Node 17+ resolves "localhost" to ::1 first, so the
    // default bind listens on IPv6 only — and Chrome, which tries 127.0.0.1,
    // then gets ERR_CONNECTION_REFUSED on a server that curl can reach.
    host: '127.0.0.1',
    proxy: {
      // Same reason on the proxy side: uvicorn listens on 127.0.0.1, so a
      // "localhost" target would resolve to ::1 and every /api call would 500.
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // three.js is ~600kB on its own; splitting it keeps the app chunk
        // small enough that navigation stays instant on a cold cache.
        manualChunks: {
          three: ['three'],
          react: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
});
