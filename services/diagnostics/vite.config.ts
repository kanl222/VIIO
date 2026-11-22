import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: { preserveSymlinks: true },
  server: { host: '127.0.0.1', port: 5174, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:3010', changeOrigin: true,
      configure(proxy) { proxy.on('proxyReq', req => req.setHeader('Origin', 'http://127.0.0.1:3010')); } } } },
});
