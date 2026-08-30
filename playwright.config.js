const { defineConfig } = require("playwright/test");

module.exports = defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:8766",
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: '".venv\\Scripts\\python.exe" -m autotrade dashboard --port 8766',
    url: "http://127.0.0.1:8766/api/state",
    timeout: 60_000,
    reuseExistingServer: false,
  },
});

