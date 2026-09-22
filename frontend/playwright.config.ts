import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:8092',
    trace: 'retain-on-failure',
    launchOptions: process.env.REOUI_BROWSER_PATH ? {executablePath: process.env.REOUI_BROWSER_PATH} : {},
  },
  webServer: {
    command: '../.venv/bin/python ../scripts/browser_fixture.py',
    url: 'http://127.0.0.1:8092/api/health',
    reuseExistingServer: false,
    timeout: 90000,
  },
  projects: [
    {name:'desktop',use:{viewport:{width:1440,height:1000}}},
    {name:'tablet',use:{viewport:{width:900,height:1100}}},
    {name:'mobile',use:{viewport:{width:390,height:844},isMobile:true,hasTouch:true}},
  ],
});
