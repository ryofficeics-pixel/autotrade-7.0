const { test, expect } = require("playwright/test");

test("paper dashboard is truthful and controls fail closed", async ({ page }) => {
  const state = {
    mode: "PAPER",
    venue: "GATE",
    trading_state: "HALTED",
    engine: { status: "SIMULATION_READY", nautilus_version: "test", risk_engine_enabled: true },
    data: { status: "LIVE", source: "GATE_PUBLIC_REST", latency_ms: 18, age_seconds: 0.4, last_event_utc: "2026-08-27T01:00:00Z", error: null },
    portfolio: { equity_usdt: 300, daily_pnl_usdt: 0, drawdown_pct: 0, trades_today: 0, open_positions: 0, fees_usdt: 0, slippage_usdt: 0 },
    open_trade: null,
    trade_history: [{ closed_at: "2026-08-27T00:58:00Z", symbol: "ETH_USDT", side: "LONG", quantity: 0.01, open_price: 2480.5, close_price: 2490.5, realized_pnl_usdt: 0.08, pnl_pct: 0.3225, fee_usdt: 0.025, reason: "TAKE_PROFIT" }],
    orders: 0,
    strategy: { name: "REST Momentum", symbol: "ETH_USDT", status: "PAUSED", armed: false, expected_gross_bps: 0, expected_cost_bps: 0, expected_net_bps: 0, last_signal: null },
    tradingview: { enabled: false, status: "DISABLED", advisory_only: true, execution_influence: "NONE", symbol: null, expected_symbol: "GATE:ETHUSDT.P", timeframe: null, expected_timeframe: "5", bias: "UNAVAILABLE", confidence: null, regime: "UNAVAILABLE", latency_ms: null, freshness_ms: null, pine_signal: "UNAVAILABLE", nautilus_agreement: "UNAVAILABLE", error: null },
    markets: [
      { symbol: "BTC_USDT", last: 78713.6, change_pct: -0.08, bid: 78702.7, ask: 78702.8, spread_bps: 0.013, volume_quote: 29360000000, funding_rate: -0.000007, screen_score: 62.4, selected: true, rejection: null },
      { symbol: "ETH_USDT", last: 2491.54, change_pct: 1.61, bid: 2491.39, ask: 2491.4, spread_bps: 0.04, volume_quote: 3760000000, funding_rate: 0.000028, screen_score: 68.1, selected: true, rejection: null },
    ],
    active_symbols: 2,
    alerts: ["REST Momentum is paused."],
    controls: { pause_allowed: false, resume_allowed: true, flatten_allowed: false },
  };
  await page.route("**/api/**", async (route) => {
    if (route.request().url().endsWith("/resume")) {
      state.trading_state = "ACTIVE";
      state.controls.pause_allowed = true;
      state.controls.resume_allowed = false;
      state.strategy.armed = true;
      state.strategy.status = "WARMING_UP";
    } else if (route.request().url().endsWith("/pause")) {
      state.trading_state = "PAUSED";
      state.controls.pause_allowed = false;
      state.controls.resume_allowed = true;
      state.strategy.armed = false;
      state.strategy.status = "PAUSED";
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(state) });
  });
  await page.goto("/");
  await expect(page).toHaveTitle("Scalper Paper Operations");
  await expect(page.getByText("PAPER", { exact: true })).toBeVisible();
  await expect(page.getByText("SIMULATION ONLY", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /live/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "FLATTEN PAPER POSITIONS" })).toBeDisabled();
  await expect(page.locator("#data-status")).toHaveText("DATA LIVE", { timeout: 20_000 });
  await expect(page.locator("#market-rows tr")).toHaveCount(2, { timeout: 20_000 });
  await expect(page.locator("#open-trade-ticker")).toBeHidden();
  await expect(page.locator("#trade-rows tr")).toHaveCount(1);
  await expect(page.locator("#trade-rows")).toContainText("$2,480.50");
  await expect(page.locator("#trade-rows")).toContainText("+$0.08");
  await expect(page.locator("#trade-rows")).toContainText("+0.323%");
  await expect(page.locator("#trade-rows")).toContainText("$0.03");
  await expect(page.locator("#tv-status")).toHaveText("DISABLED");
  await page.screenshot({ path: "test-results/dashboard-overview.png", fullPage: true });

  state.portfolio.open_positions = 1;
  state.open_trade = { symbol: "ETH_USDT", side: "LONG", quantity: 0.01, entry_price: 2491.54, current_price: 2492.25 };
  await page.evaluate(() => refresh());
  await expect(page.locator("#open-trade-ticker")).toHaveText("OPEN PAPER TRADE · ETH / USDT · LONG · ENTRY $2,491.54 · NOW $2,492.25");
  await expect(page.locator("#open-trade-ticker")).toBeVisible();
  await page.screenshot({ path: "test-results/dashboard-open-trade.png", fullPage: true });
  state.portfolio.open_positions = 0;
  state.open_trade = null;

  await page.getByRole("button", { name: "RESUME PAPER" }).click();
  await expect(page.locator("#trading-state")).toHaveText("ACTIVE");
  await expect(page.locator("#strategy-badge")).toHaveText("ARMED");
  await page.getByRole("button", { name: "PAUSE NEW ENTRIES" }).click();
  await expect(page.locator("#trading-state")).toHaveText("PAUSED");

  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
});

test("backend loss is loud and disables controls", async ({ page }) => {
  await page.route("**/api/state", (route) => route.fulfill({ status: 503, body: "offline" }));
  await page.goto("/");
  await expect(page.locator("#connection-banner")).toBeVisible();
  await expect(page.locator("#trading-state")).toHaveText("UNKNOWN");
  await expect(page.getByRole("button", { name: "PAUSE NEW ENTRIES" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "RESUME PAPER" })).toBeDisabled();
});

test("TradingView card isolates states, updates, and stays responsive", async ({ page }) => {
  const state = {
    mode: "PAPER",
    venue: "GATE",
    trading_state: "ACTIVE",
    engine: { status: "SIMULATION_READY", nautilus_version: "test", risk_engine_enabled: true },
    data: { status: "LIVE", source: "GATE_PUBLIC_REST", latency_ms: 18, age_seconds: 0.4, last_event_utc: "2026-08-27T01:00:00Z", error: null },
    portfolio: { equity_usdt: 300, daily_pnl_usdt: 0, drawdown_pct: 0, trades_today: 0, open_positions: 0, fees_usdt: 0, slippage_usdt: 0 },
    open_trade: null,
    trade_history: [],
    orders: 0,
    strategy: { name: "REST Momentum", symbol: "ETH_USDT", status: "WAITING_EDGE", armed: true, expected_gross_bps: 8, expected_cost_bps: 17, expected_net_bps: -9, last_signal: null },
    tradingview: { enabled: true, status: "CONNECTED", advisory_only: true, execution_influence: "NONE", symbol: "GATE:ETHUSDT.P", expected_symbol: "GATE:ETHUSDT.P", timeframe: "5", expected_timeframe: "5", price: 2492.25, price_divergence_pct: 0.0285, bias: "LONG", confidence: 0.68, regime: "TRENDING", latency_ms: 84, freshness_ms: 1200, pine_signal: "LONG", nautilus_agreement: "AGREE", indicator_count: 2, indicators: [{ study: "RSI", name: "RSI", value: 57.4 }], error: null },
    markets: [],
    active_symbols: 0,
    alerts: ["TradingView is secondary and cannot execute orders."],
    controls: { pause_allowed: true, resume_allowed: false, flatten_allowed: false },
  };
  const consoleErrors = [];
  const apiErrors = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("response", (response) => { if (response.url().includes("/api/") && !response.ok()) apiErrors.push(response.status()); });
  await page.route("**/api/state", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(state) }));

  await page.goto("/");
  await expect(page.locator("#tv-status")).toHaveText("CONNECTED");
  await expect(page.locator("#tv-bias")).toHaveText("LONG / 68%");
  await expect(page.locator("#tv-price")).toHaveText("$2,492.25 / +0.0285%");
  await expect(page.locator("#tv-indicators")).toHaveText("2");
  await expect(page.locator("#tv-timing")).toHaveText("84 ms / 1.2 s");
  await page.screenshot({ path: "test-results/tradingview-connected.png", fullPage: true });

  state.tradingview.status = "DEGRADED";
  state.tradingview.latency_ms = 750;
  state.tradingview.error = "Chart symbol differs from the paper strategy";
  await page.evaluate(() => refresh());
  await expect(page.locator("#tv-status")).toHaveText("DEGRADED");
  await expect(page.locator("#tv-timing")).toContainText("750 ms");

  state.tradingview.status = "DISCONNECTED";
  await page.evaluate(() => refresh());
  await expect(page.locator("#tv-status")).toHaveText("DISCONNECTED");
  await expect(page.locator("#trading-state")).toHaveText("ACTIVE");
  await page.screenshot({ path: "test-results/tradingview-disconnected.png", fullPage: true });

  state.tradingview.status = "UNAVAILABLE";
  await page.evaluate(() => refresh());
  await expect(page.locator("#tv-status")).toHaveText("UNAVAILABLE");

  state.tradingview.status = "CONNECTED";
  state.tradingview.symbol = "GATEIO:THISISANINTENTIONALLYLONGTOKENUSDT.P";
  state.tradingview.error = null;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => refresh());
  await expect(page.locator("#tradingview-card")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/tradingview-mobile.png", fullPage: true });
  expect(consoleErrors).toEqual([]);
  expect(apiErrors).toEqual([]);
});
