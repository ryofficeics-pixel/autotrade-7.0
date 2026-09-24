const { test, expect } = require("playwright/test");

test("paper dashboard is truthful and controls fail closed", async ({ page }) => {
  const state = {
    mode: "PAPER",
    venue: "GATE",
    trading_state: "HALTED",
    engine: { status: "SIMULATION_READY", nautilus_version: "test", risk_engine_enabled: true },
    accounting: { state: "VALID", reason: null, checkpoint_sequence: 4, last_event_sequence: 12, tolerance_usdt: "0.00000001" },
    risk: { state: "OK", risk_day_utc: "2026-08-27", day_start_equity_usdt: "300", xau_risk_per_trade_usdt: 0.15, xau_leverage: 1 },
    execution_model: { state: "PAPER_SIM", queue_model: "NOT_MODELED" },
    run: { run_id: "11111111-1111-4111-8111-111111111111", session_id: "22222222-2222-4222-8222-222222222222", git_commit: "abcdef0123456789", config_hash: "1234567890abcdef", event_schema_version: 3 },
    storage: { free_disk_bytes: 20000000000, free_disk_percent: 50, safe: true },
    data: { status: "LIVE", source: "GATE_PUBLIC_REST", latency_ms: 18, age_seconds: 0.4, last_event_utc: "2026-08-27T01:00:00Z", error: null },
    portfolio: { equity_usdt: 300, daily_pnl_usdt: 0, drawdown_pct: 0, trades_today: 0, open_positions: 0, fees_usdt: 0, slippage_usdt: 0 },
    profitability: {
      normal: { net_pnl_usdt: -2.05 }, recovery: { net_pnl_usdt: 5.09 }, manual: { net_pnl_usdt: -0.04 },
      full_run: { gross_price_pnl_usdt: 4.12, fees_usdt: 1.12, net_pnl_usdt: 3, turnover_usdt: 2200 },
      spread_cost_usdt: null, modeled_slippage_usdt: null, current_session_drawdown_pct: 0.2,
      current_utc_day_drawdown_pct: 0.4, current_wib_day_drawdown_pct: 0.5,
      current_run_start_drawdown_pct: 0, current_all_time_high_drawdown_pct: 0.7,
      current_normal_strategy_drawdown_pct: 1.1, current_open_risk_usdt: 0,
      realized_daily_risk_usage_usdt: 4.7, trades_per_active_hour: null,
      market_classes: {
        ALL: { trades: 2, net_pnl_usdt: 3, gross_price_pnl_usdt: 4.12, fees_usdt: 1.12, turnover_usdt: 2200 },
        CRYPTO: { trades: 1, net_pnl_usdt: 2, gross_price_pnl_usdt: 2.5, fees_usdt: 0.5, turnover_usdt: 1200 },
        XAU: { trades: 1, net_pnl_usdt: 1, gross_price_pnl_usdt: 1.62, fees_usdt: 0.62, turnover_usdt: 1000 },
      },
    },
    experiments: { mode: "UNCHANGED_BASELINE", exit_variant: "BASELINE_FULL_TP", rejection_counts: {}, evidence_status: "INSUFFICIENT EVIDENCE" },
    open_trade: null,
    trade_history: [{ closed_at: "2026-08-27T00:58:00Z", symbol: "ETH_USDT", side: "LONG", quantity: 0.01, open_price: 2480.5, close_price: 2490.5, realized_pnl_usdt: 0.08, pnl_pct: 0.3225, fee_usdt: 0.025, reason: "TAKE_PROFIT" }],
    orders: 0,
    strategy: { name: "REST Momentum", symbol: "ETH_USDT", status: "PAUSED", armed: false, expected_gross_bps: 0, expected_cost_bps: 0, expected_net_bps: 0, last_signal: null, xau: { direction: "LONG", confidence: 0.78, regime: "TRENDING_UP", volatility: "NORMAL", confirmation: "AGREE", reasons: ["QUALIFIED"], feed_health: { XAU_USDT: "HEALTHY", XAUT_USDT: "HEALTHY", PAXG_USDT: "HEALTHY" }, contract_status: "VALID" } },
    market_scope: { active_scope: "WIDE_CRYPTO", requested_scope: null, switch_status: "ACTIVE", switch_policy: null, open_positions: 0, blocking_reason: null, execution_symbol: "DYNAMIC" },
    entry_v3: { status: "LIVE", strategy_status: "SHADOW", execution_enabled: false, candidate_count: 3, accepted_count: 1, rejected_count: 2, latest_candidate: { decision: "REJECTED", direction: "LONG" } },
    ama_control: {
      name: "XAU_KAMA20_CONTROL_V2", status: "COLLECTING", execution_enabled: false, execution_influence: "NONE",
      evidence_integrity: "VALID", observations: 74, price: 3680.25,
      kama: { "10": 3679.8, "20": 3679.2, "50": 3678.4 }, atr_proxy_bps: 2.1, hysteresis_bps: 17.27,
      regime: "UPTREND", raw_signal: "LONG", filtered_signal: "WAIT", quality_decision: "HYSTERESIS_BAND",
      reference: { status: "HEALTHY", dislocation_bps: 4.08 }, counterfactual_pending: 18,
      strategies: {
        CURRENT_PAPER_BASELINE: { status: "RUN_LEDGER_NOT_SAME_TIMELINE", trades: 315, net_pnl_usdt: -18.39, profit_factor: 0.5, max_drawdown_usdt: 19.2, sample_status: "NOT_COMPARABLE" },
        XAU_KAMA20_RAW_V2: { status: "SHADOW", trades: 4, net_pnl_usdt: -0.12, profit_factor: 0.72, max_drawdown_usdt: 0.2, sample_status: "INSUFFICIENT_EVIDENCE" },
        XAU_KAMA20_FILTERED_V2: { status: "SHADOW", trades: 2, net_pnl_usdt: 0.03, profit_factor: 1.1, max_drawdown_usdt: 0.04, sample_status: "INSUFFICIENT_EVIDENCE" },
        XAU_FAIR_VALUE_V1: { status: "REFERENCE_ONLY", trades: 0 },
        XAU_COMBINED_V1: { status: "NOT_IMPLEMENTED", trades: 0 },
        ENTRY_V3: { status: "SEPARATE_CAPTURE_TIMELINE", trades: 0 },
      },
      comparison: { status: "INSUFFICIENT_EVIDENCE", complexity_alpha: "UNPROVEN", promotion_eligible: false },
    },
    tradingview: { enabled: false, status: "DISABLED", advisory_only: true, execution_influence: "NONE", symbol: null, expected_symbol: "GATE:ETHUSDT.P", timeframe: null, expected_timeframe: "5", bias: "UNAVAILABLE", confidence: null, regime: "UNAVAILABLE", latency_ms: null, freshness_ms: null, pine_signal: "UNAVAILABLE", nautilus_agreement: "UNAVAILABLE", error: null },
    markets: [
      { symbol: "BTC_USDT", last: 78713.6, change_pct: -0.08, bid: 78702.7, ask: 78702.8, spread_bps: 0.013, volume_quote: 29360000000, funding_rate: -0.000007, screen_score: 62.4, selected: true, rejection: null },
      { symbol: "ETH_USDT", last: 2491.54, change_pct: 1.61, bid: 2491.39, ask: 2491.4, spread_bps: 0.04, volume_quote: 3760000000, funding_rate: 0.000028, screen_score: 68.1, selected: true, rejection: null },
    ],
    active_symbols: 2,
    alerts: ["REST Momentum is paused."],
    controls: { pause_allowed: false, resume_allowed: true, flatten_allowed: false },
    research: {
      status: "READY",
      dataset: { dataset_id: "xau-test-dataset", row_count: 7665 },
      experiment_identity: {
        dataset_id: "xau-test-dataset", dataset_hash: "a".repeat(64), git_commit: "abcdef0123456789",
        config_hash: "b".repeat(64), execution_profile: "BASELINE", experiment_ids: ["experiment-001"],
      },
      strategy_lab: [{
        strategy: "XAU_FAIR_VALUE_V1", version: "1", lifecycle_state: "EXPERIMENTAL", sample: 1,
        gross_expectancy_bps: 2.6, net_expectancy_bps: -14.42, profit_factor: 0,
        maximum_drawdown_bps: 14.42, test_result: null, final_holdout_result: null,
        stress_result: { net_expectancy_bps: -22.1 }, parameter_stability: "INSUFFICIENT_EVIDENCE",
        promotion_eligible: false, reason_blocked: ["EXECUTION_MODEL_NOT_VALIDATED"],
      }],
      unsupported_strategies: [{
        strategy_id: "MICROSTRUCTURE_ENTRY_V3", strategy_version: "3",
        lifecycle_state: "EXPERIMENTAL", status: "NOT_RECONSTRUCTABLE",
        reason: "L2 evidence unavailable", promotion_eligible: false,
      }],
      candidate_funnels: {
        KAMA_FILTERED_CAPTURED: {
          generated: 3058, accepted: 1, rejected: 3057,
          binding_gates: [{ gate: "REGIME", reject_count: 1722 }],
        },
      },
      no_trade_value: {
        KAMA_FILTERED_CAPTURED: {
          rejected: 3050, avoided_losses: 2884, missed_profitable_trades: 165,
          net_filter_benefit_bps: 130.4, time_in_no_trade_seconds: 88000,
        },
      },
      execution_edge: {
        XAU_FAIR_VALUE_V1: {
          gross_move_bps: 2.6, spread_cost_bps: 1.8, fee_cost_bps: 10,
          slippage_cost_bps: 2, latency_cost_bps: 1, adverse_selection_bps: 1.2,
          funding_cost_bps: 0.02, impact_bps: 1, net_edge_bps: -14.42,
        },
      },
      lifecycle: {
        "REST_MOMENTUM_TOURNAMENT_V2:2": {
          strategy_id: "REST_MOMENTUM_TOURNAMENT_V2", strategy_version: "2",
          state: "RETIRED", reason: "NEGATIVE_NORMAL_EXPECTANCY",
        },
      },
      universe: {
        selected: [{
          symbol: "BTC_USDT", tradability: "AVAILABLE", snapshot_quote_volume_usdt: 120000000,
          snapshot_spread_bps: 0.2, listing_age_days: 2500, funding_rate: "0.0001",
          rejection_reasons: [],
        }],
        rejected: [{
          symbol: "NEW_USDT", tradability: "REJECTED", snapshot_quote_volume_usdt: 900000,
          snapshot_spread_bps: 20, listing_age_days: 2, funding_rate: null,
          rejection_reasons: ["NEW_LISTING", "LOW_LIQUIDITY"],
        }],
      },
      top_candidates: [{
        symbol: "BTC_USDT", direction: "LONG", normalized_momentum: 1.25, percentile: 0.95,
        breakout_distance_bps: 22, volatility_ratio: 1.4, volume_ratio: 1.6,
        expected_move_bps: 80, execution_cost_bps: 20, edge_cost_ratio: 4,
        decision: "ACCEPTED", first_rejection_reason: null,
      }],
      overfitting_audit: {
        experiments_run: 7, families_tested: 3, parameter_variants_tested: 4,
        holdout_access_count: 1, deflated_sharpe_ratio: { status: "UNAVAILABLE" },
        probability_of_backtest_overfitting: { status: "UNAVAILABLE" },
        synthetic_null: { status: "AVAILABLE" },
      },
      cpcv: { CROSS_SECTIONAL_BREAKOUT_V1: { number_of_paths: 28, positive_path_fraction: 0.5 } },
      paper_eligible_strategies: [],
      automatic_promotion: false,
      live_trading: "UNAVAILABLE",
    },
  };
  await page.route("**/api/**", async (route) => {
    if (route.request().url().endsWith("/api/market-scope") && route.request().method() === "POST") {
      const payload = route.request().postDataJSON();
      state.market_scope = { ...state.market_scope, active_scope: payload.scope, switch_status: "ACTIVE", switch_policy: payload.switch_policy };
      if (payload.scope === "XAU_ONLY" && !state.markets.some((market) => market.symbol === "XAU_USDT")) {
        state.markets.push(
          { symbol: "XAU_USDT", last: 3680.25, change_pct: 0.8, bid: 3680.2, ask: 3680.3, spread_bps: 0.27, volume_quote: 120000000, funding_rate: 0, screen_score: 0, selected: false, rejection: "XAU PROFILE" },
          { symbol: "XAUT_USDT", last: 3678.1, change_pct: 0.7, bid: 3678, ask: 3678.2, spread_bps: 0.54, volume_quote: 50000000, funding_rate: 0, screen_score: 0, selected: false, rejection: "REFERENCE" },
          { symbol: "PAXG_USDT", last: 3679.4, change_pct: 0.75, bid: 3679.3, ask: 3679.5, spread_bps: 0.54, volume_quote: 60000000, funding_rate: 0, screen_score: 0, selected: false, rejection: "REFERENCE" },
        );
      }
    } else if (route.request().url().endsWith("/resume")) {
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
  await expect(page.locator("#accounting-state")).toHaveText("ACCOUNTING VALID");
  await expect(page.locator("#halt-timer")).toBeHidden();
  await expect(page.locator("#run-session")).toHaveText("11111111 / 22222222");
  await expect(page.locator("#market-rows tr")).toHaveCount(2, { timeout: 20_000 });
  await expect(page.locator("#open-trade-ticker")).toBeHidden();
  await expect(page.locator("#trade-rows tr")).toHaveCount(1);
  await expect(page.locator("#trade-rows")).toContainText("$2,480.50");
  await expect(page.locator("#trade-rows")).toContainText("+$0.08");
  await expect(page.locator("#trade-rows")).toContainText("+0.323%");
  await expect(page.locator("#trade-rows")).toContainText("$0.03");
  await expect(page.locator("#normal-net")).toHaveText("-$2.05");
  await expect(page.locator("#recovery-net")).toHaveText("+$5.09");
  await expect(page.locator("#evidence-status")).toHaveText("INSUFFICIENT EVIDENCE");
  await expect(page.locator("#analytics")).toContainText("Recovery and manual results are excluded");
  await page.locator("#analytics-scope").selectOption("XAU");
  await expect(page.locator("#full-net")).toHaveText("+$1.00");
  await page.locator("#analytics-scope").selectOption("ALL");
  await expect(page.locator("#tv-status")).toHaveText("DISABLED");
  await expect(page.locator("#ama-control")).toBeVisible();
  await expect(page.locator("#ama-status")).toHaveText("COLLECTING");
  await expect(page.locator("#ama-signals")).toHaveText("LONG / WAIT");
  await expect(page.locator("#ama-comparison-rows tr")).toHaveCount(6);
  await expect(page.locator("#ama-complexity")).toContainText("UNPROVEN");
  await expect(page.locator("#ama-strategy-status")).toContainText("EXECUTION DISABLED");
  await expect(page.locator("#research-status")).toHaveText("NO STRATEGY ELIGIBLE");
  await expect(page.locator("#research-dataset")).toHaveText("xau-test-dataset");
  await expect(page.locator("#research-strategy-rows")).toContainText("XAU_FAIR_VALUE_V1");
  await expect(page.locator("#research-strategy-rows tr")).toHaveCount(2);
  await expect(page.locator("#research-funnel-rows")).toContainText("REGIME");
  await expect(page.locator("#research-universe-rows")).toContainText("BTC_USDT");
  await expect(page.locator("#research-universe-rows")).toContainText("NEW LISTING");
  await expect(page.locator("#research-candidate-rows")).toContainText("ACCEPTED");
  await expect(page.locator("#research-overfit-rows")).toContainText("28");
  await expect(page.locator("#research-lifecycle-rows")).toContainText("RETIRED");
  await page.screenshot({ path: "test-results/dashboard-overview.png", fullPage: true });

  await page.getByRole("button", { name: "XAU ONLY" }).click();
  await expect(page.locator("#scope-status")).toHaveText("ACTIVE: XAU ONLY");
  await expect(page.locator("#xau-panel")).toBeVisible();
  await expect(page.locator("#xau-signal")).toHaveText("LONG");
  await expect(page.locator("#xaut-health")).toHaveText("HEALTHY");
  await expect(page.locator("#markets")).toBeHidden();
  await page.screenshot({ path: "test-results/dashboard-xau-scope.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/dashboard-xau-scope-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole("button", { name: "WIDE CRYPTO" }).click();
  await expect(page.locator("#scope-status")).toHaveText("ACTIVE: WIDE CRYPTO");

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
  await expect(page.locator("#strategy-badge")).toHaveText("V3 SHADOW");
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

test("risk halt timing never promises automatic resume", async ({ page }) => {
  const riskDay = new Date().toISOString().slice(0, 10);
  const state = {
    mode: "PAPER",
    venue: "GATE",
    trading_state: "HALTED",
    engine: { status: "SIMULATION_READY", risk_engine_enabled: true },
    accounting: { state: "VALID", checkpoint_sequence: 1, last_event_sequence: 1, tolerance_usdt: "0.00000001" },
    risk: { state: "DAILY_LOSS", risk_day_utc: riskDay },
    execution_model: { state: "PAPER_SIM", queue_model: "NOT_MODELED" },
    run: {},
    storage: {},
    data: { status: "LIVE", source: "GATE_PUBLIC_REST", latency_ms: 10, age_seconds: 0.1, last_event_utc: new Date().toISOString() },
    portfolio: { equity_usdt: 294, daily_pnl_usdt: -6, drawdown_pct: 2, trades_today: 3, open_positions: 0 },
    open_trade: null,
    trade_history: [],
    orders: 0,
    strategy: { symbol: "ETH_USDT", status: "PAUSED", armed: false, expected_net_bps: 0 },
    tradingview: {},
    markets: [],
    active_symbols: 0,
    alerts: ["Paper risk limit reached (DAILY_LOSS); new entries are halted."],
    controls: { pause_allowed: false, resume_allowed: false, flatten_allowed: false },
  };
  await page.route("**/api/state", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(state) }));
  await page.goto("/");
  await expect(page.locator("#halt-timer")).toContainText("MANUAL REVIEW ELIGIBLE IN");
  await expect(page.locator("#halt-timer")).toContainText("NO AUTOMATIC RESUME");

  state.risk.state = "DAILY_LOSS_REVIEW";
  state.controls.resume_allowed = true;
  await page.evaluate(() => refresh());
  await expect(page.locator("#halt-timer")).toHaveText("NO AUTOMATIC LIFT · DAILY LOSS REVIEW · MANUAL REVIEW REQUIRED");
  await expect(page.getByRole("button", { name: "RESUME PAPER" })).toBeEnabled();

  state.risk.state = "MAX_DRAWDOWN";
  state.controls.resume_allowed = false;
  state.controls.new_paper_run_allowed = true;
  state.alerts = ["Paper risk limit reached (MAX_DRAWDOWN); new entries are halted."];
  await page.evaluate(() => refresh());
  await expect(page.locator("#halt-timer")).toContainText("MANUAL REVIEW ELIGIBLE IN");
  await expect(page.locator("#halt-timer")).toContainText("NO AUTOMATIC RESUME");
  await expect(page.getByRole("button", { name: "RESUME PAPER" })).toBeDisabled();
  const newRun = page.getByRole("button", { name: "START NEW PAPER RUN" });
  await expect(newRun).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/max-drawdown-new-run-mobile.png", fullPage: true });

  state.risk.state = "MAX_DRAWDOWN_REVIEW";
  state.controls.resume_allowed = true;
  state.controls.new_paper_run_allowed = false;
  await page.evaluate(() => refresh());
  await expect(page.locator("#halt-timer")).toHaveText("NO AUTOMATIC LIFT · MAX DRAWDOWN REVIEW · MANUAL REVIEW REQUIRED");
  await expect(page.getByRole("button", { name: "RESUME PAPER" })).toBeEnabled();

  state.risk.state = "MAX_DRAWDOWN";
  state.controls.resume_allowed = false;
  state.controls.new_paper_run_allowed = true;
  await page.evaluate(() => refresh());
  await page.route("**/api/control/new-paper-run", (route) => {
    expect(route.request().postDataJSON()).toEqual({ confirm_new_run: true });
    state.trading_state = "ACTIVE";
    state.risk.state = "OK";
    state.portfolio = { ...state.portfolio, equity_usdt: 300, daily_pnl_usdt: 0, drawdown_pct: 0, trades_today: 0 };
    state.strategy.armed = true;
    state.controls = { pause_allowed: true, resume_allowed: false, flatten_allowed: false, new_paper_run_allowed: false };
    state.alerts = ["All screened pairs are monitored; one highest-confidence eligible pair may trade."];
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(state) });
  });
  page.once("dialog", (dialog) => dialog.accept());
  await newRun.click();
  await expect(page.locator("#trading-state")).toHaveText("ACTIVE");
  await expect(newRun).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("TradingView card isolates states, updates, and stays responsive", async ({ page }) => {
  const state = {
    mode: "PAPER",
    venue: "GATE",
    trading_state: "ACTIVE",
    engine: { status: "SIMULATION_READY", nautilus_version: "test", risk_engine_enabled: true },
    accounting: { state: "VALID", reason: null, checkpoint_sequence: 1, last_event_sequence: 0, tolerance_usdt: "0.00000001" },
    risk: { state: "OK", risk_day_utc: "2026-08-27", day_start_equity_usdt: "300" },
    execution_model: { state: "PAPER_SIM", queue_model: "NOT_MODELED" },
    run: { run_id: "33333333-3333-4333-8333-333333333333", session_id: "44444444-4444-4444-8444-444444444444", git_commit: "abcdef0123456789", config_hash: "1234567890abcdef", event_schema_version: 3 },
    storage: { free_disk_bytes: 20000000000, free_disk_percent: 50, safe: true },
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

test("accounting failure is prominent, disables resume, and symbol quarantine stays local", async ({ page }) => {
  const state = {
    mode: "PAPER",
    venue: "GATE",
    trading_state: "HALTED",
    engine: { status: "SIMULATION_READY", nautilus_version: "test", risk_engine_enabled: true },
    accounting: { state: "INVALID", reason: "checkpoint/ledger equity mismatch", checkpoint_sequence: 7, last_event_sequence: 13, tolerance_usdt: "0.00000001" },
    risk: { state: "STATE_INVALID", risk_day_utc: "2026-08-27", day_start_equity_usdt: "300" },
    execution_model: { state: "PAPER_SIM", queue_model: "NOT_MODELED" },
    run: { run_id: "55555555-5555-4555-8555-555555555555", session_id: "66666666-6666-4666-8666-666666666666", git_commit: "abcdef0123456789", config_hash: "1234567890abcdef", event_schema_version: 3 },
    storage: { free_disk_bytes: 20000000000, free_disk_percent: 50, safe: true },
    data: { status: "LIVE", source: "GATE_PUBLIC_REST", latency_ms: 20, age_seconds: 0.2, last_event_utc: "2026-08-27T01:00:00Z", error: null },
    portfolio: { equity_usdt: 292.99226546, daily_pnl_usdt: -7.00773454, drawdown_pct: 2.34, trades_today: 51, open_positions: 0, fees_usdt: 1.54, slippage_usdt: 0.1 },
    open_trade: null,
    trade_history: [],
    orders: 0,
    strategy: { name: "REST Momentum", symbol: "BTC_USDT", status: "STATE_INVALID", armed: false, monitored_symbols: 2, confidence: 0, expected_gross_bps: 0, expected_cost_bps: 0, expected_net_bps: 0, last_signal: null },
    tradingview: { enabled: false, status: "DISABLED", advisory_only: true, execution_influence: "NONE" },
    markets: [
      { symbol: "LOW_USDT", last: 0.004, change_pct: 1, bid: 0.004, ask: 0.0041, spread_bps: 2, volume_quote: 10000000, funding_rate: 0, screen_score: 50, selected: true, rejection: null, signal_confidence: 0, signal_status: "QUARANTINED", quarantine_reason: "raw quote crossed" },
      { symbol: "BTC_USDT", last: 78000, change_pct: 1, bid: 77999, ask: 78001, spread_bps: 0.25, volume_quote: 100000000, funding_rate: 0, screen_score: 70, selected: true, rejection: null, signal_confidence: 0.5, signal_status: "WAITING_EDGE", quarantine_reason: null },
    ],
    active_symbols: 2,
    alerts: ["Persistent paper state is invalid."],
    controls: { pause_allowed: false, resume_allowed: false, flatten_allowed: false },
  };
  await page.route("**/api/state", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(state) }));
  await page.goto("/");
  await expect(page.locator("#accounting-state")).toHaveText("ACCOUNTING INVALID");
  await expect(page.locator("#accounting-detail")).toContainText("equity mismatch");
  await expect(page.getByRole("button", { name: "RESUME PAPER" })).toBeDisabled();
  await expect(page.locator("#market-rows")).toContainText("QUARANTINED");
  await expect(page.locator("#market-rows")).toContainText("WAITING EDGE");

  const analyze = page.getByRole("button", { name: "ANALYZE & AUTO-FIX" });
  await expect(analyze).toBeEnabled();
  let finishAnalysis;
  const pending = new Promise((resolve) => { finishAnalysis = resolve; });
  await page.route("**/api/control/analyze-repair", async (route) => {
    expect(route.request().method()).toBe("POST");
    await pending;
    state.recovery = {
      status: "BLOCKED", code: "ACCOUNTING_INVALID", checked_at_utc: "2026-09-03T06:00:00Z",
      trigger: "HALTED: checkpoint/ledger equity mismatch", detail: "checkpoint/ledger equity mismatch",
      checks: ["Checkpoint and full fill ledger: INVALID", "Public market data: LIVE"], actions: [],
      next_action: "Investigate or restore verified evidence; automatic reset is forbidden.",
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(state) });
  });
  await analyze.click();
  await expect(page.locator("#analyze-button")).toHaveText("ANALYZING…");
  await expect(page.locator("#analyze-button")).toBeDisabled();
  await page.evaluate(() => refresh());
  await expect(page.locator("#analyze-button")).toBeDisabled();
  finishAnalysis();
  await expect(page.locator("#recovery-status")).toContainText("ANALYSIS BLOCKED");
  await expect(page.locator("#recovery-next")).toContainText("automatic reset is forbidden");
  await expect(page.getByRole("button", { name: "RESUME PAPER" })).toBeDisabled();
  await expect(analyze).toBeEnabled();
  await page.reload();
  await expect(page.locator("#recovery-status")).toContainText("ANALYSIS BLOCKED");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/analysis-blocked-mobile.png", fullPage: true });

  await page.route("**/api/control/analyze-repair", (route) => route.fulfill({
    status: 409, contentType: "application/json", body: JSON.stringify({ error: "Analysis is already running." }),
  }));
  await analyze.click();
  await expect(page.locator("#control-feedback")).toContainText("Analysis is already running.");
  await expect(analyze).toBeEnabled();

  await page.route("**/api/control/analyze-repair", (route) => {
    state.accounting.state = "VALID";
    state.risk.state = "OK";
    state.trading_state = "ACTIVE";
    state.strategy.armed = true;
    state.controls.pause_allowed = true;
    state.recovery = {
      ...state.recovery, status: "FIXED", code: "HEALTHY", detail: "Safety checks pass; PAPER entries are enabled.",
      checks: ["Checkpoint and full fill ledger: VALID", "Public market data: LIVE"],
      actions: ["Resumed PAPER entries after safety checks passed."], next_action: "The strategy still waits for a qualified signal.",
    };
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(state) });
  });
  await analyze.click();
  await expect(page.locator("#recovery-status")).toContainText("ANALYSIS FIXED");
  await expect(page.locator("#trading-state")).toHaveText("ACTIVE");
  await expect(page.locator("#recovery-checks")).toContainText("Resumed PAPER entries");
  await expect(page.locator("#control-feedback")).toBeHidden();
  await expect(analyze).toBeEnabled();
});
