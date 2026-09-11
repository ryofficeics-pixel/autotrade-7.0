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
    command: '".venv\\Scripts\\python.exe" -c "from dataclasses import replace; from pathlib import Path; from autotrade.config import load_settings; from autotrade.dashboard import run_dashboard; s=load_settings(); run_dashboard(replace(s, dashboard_port=8766, log_directory=Path(\'test-results/ui-runtime/logs\').resolve(), tradingview_enabled=False, strategy_enabled=False, entry_v3=replace(s.entry_v3, enabled=False)))"',
    url: "http://127.0.0.1:8766/api/state",
    timeout: 60_000,
    reuseExistingServer: false,
  },
});
