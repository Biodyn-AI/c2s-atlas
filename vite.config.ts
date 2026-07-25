import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/c2s-atlas/',
  build: {
    chunkSizeWarningLimit: 5000,
    rollupOptions: {
      output: {
        manualChunks: {
          plotly: ['plotly.js', 'react-plotly.js'],
        },
      },
    },
  },
  define: {
    global: 'globalThis',
  },
})
