import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig(async () => ({
  plugins: [react()],
  test: {
    exclude: ['node_modules/**'],
  },
  clearScreen: false,
  server: {
    port: 1421,
    strictPort: true,
    watch: {
      ignored: ['**/src-tauri/target/**'],
    },
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    target: ['es2021', 'chrome100'],
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
}))
