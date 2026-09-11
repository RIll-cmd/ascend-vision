import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', workers: 1,
  use: { baseURL: 'http://127.0.0.1:5178', viewport: { width: 1440, height: 1000 }, launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--enable-unsafe-swiftshader'] } },
  webServer: { command: 'npm run dev -- --port 5178 --strictPort', url: 'http://127.0.0.1:5178', reuseExistingServer: !process.env.CI },
});
