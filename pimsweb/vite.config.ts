import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies the API so `npm run dev` talks to `uvicorn pims.app:app`
// on 8080 without CORS configuration.
//
// `npm run build:demo` sets VITE_PIMS_DEMO=1, which switches lib/api.ts to the
// in-page implementation and emits one bundle for the single-file sandbox.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
})
