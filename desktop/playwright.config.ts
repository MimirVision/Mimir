import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 45_000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:1421',
    viewport: { width: 1280, height: 820 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node ./node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 1421',
    port: 1421,
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
