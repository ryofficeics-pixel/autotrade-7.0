"use strict";

const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
let controlPending = false;
let haltTiming = null;
let analyticsScope = "ALL";
let latestState = null;

function text(id, value) { $(id).textContent = value; }
function signed(value, digits = 2) { return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`; }
function price(value) {
  const maximumFractionDigits = value >= 1000 ? 2 : value >= 1 ? 4 : value >= 0.01 ? 6 : 8;
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits });
}
function metricMoney(value) {
  return Number.isFinite(value) ? `${value >= 0 ? "+" : "-"}$${money.format(Math.abs(value))}` : "N/A";
}
function metricPercent(value) { return Number.isFinite(value) ? `${value.toFixed(2)}%` : "N/A"; }
function metricBps(value) { return Number.isFinite(value) ? `${signed(value, 2)} bps` : "N/A"; }
function duration(value) {
  if (!Number.isFinite(value)) return "N/A";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return `${hours}h ${minutes}m`;
}
function status(element, label, state) {
  element.textContent = label;
  element.className = `status ${state}`;
}

function renderHaltTiming() {
  const element = $("halt-timer");
  if (!haltTiming) {
    element.hidden = true;
    return;
  }
  element.hidden = false;
  if (!["DAILY_LOSS", "MAX_DRAWDOWN"].includes(haltTiming.riskState)) {
    element.textContent = `NO AUTOMATIC LIFT · ${haltTiming.riskState.replaceAll("_", " ")} · MANUAL REVIEW REQUIRED`;
    return;
  }
  const remaining = haltTiming.reviewAt - Date.now();
  if (!Number.isFinite(remaining) || remaining <= 0) {
    element.textContent = "UTC RISK WINDOW ENDED · MANUAL REVIEW REQUIRED · NO AUTOMATIC RESUME";
    return;
  }
  const seconds = Math.ceil(remaining / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  element.textContent = `MANUAL REVIEW ELIGIBLE IN ${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")} · NO AUTOMATIC RESUME`;
}

function cell(row, value, className = "") {
  const item = document.createElement("td");
  item.textContent = value;
  if (className) item.className = className;
  row.append(item);
}

function renderMarkets(markets) {
  const body = $("market-rows");
  if (!markets.length) {
    const row = document.createElement("tr");
    const item = document.createElement("td");
    item.colSpan = 10;
    item.className = "empty";
    item.textContent = "No markets passed the current data checks.";
    row.append(item);
    body.replaceChildren(row);
    return;
  }
  body.replaceChildren(...markets.map((market) => {
    const row = document.createElement("tr");
    cell(row, market.symbol.replace("_", " / "), "symbol");
    cell(row, price(market.last));
    cell(row, `${signed(market.change_pct)}%`, market.change_pct >= 0 ? "positive" : "negative");
    cell(row, `${market.spread_bps.toFixed(2)} bp`);
    cell(row, compact.format(market.volume_quote));
    cell(row, `${signed(market.funding_rate * 100, 4)}%`, market.funding_rate <= 0 ? "positive" : "");
    cell(row, market.screen_score.toFixed(1));
    const confidence = Number.isFinite(market.signal_confidence) ? market.signal_confidence : 0;
    cell(row, `${Math.round(confidence * 100)}%`);
    cell(row, (market.signal_status || "NOT_MONITORED").replaceAll("_", " "));
    if (market.quarantine_reason) row.title = market.quarantine_reason;
    const stateCell = document.createElement("td");
    const chip = document.createElement("span");
    chip.className = `state-chip${market.selected ? "" : " rejected"}`;
    chip.textContent = market.selected ? "SELECTED" : market.rejection;
    stateCell.append(chip);
    row.append(stateCell);
    return row;
  }));
}

function renderMarketScope(state) {
  const scope = state.market_scope || {};
  const active = scope.active_scope || "UNKNOWN";
  const switchState = scope.switch_status || "FAILED";
  const switching = switchState !== "ACTIVE";
  status($("scope-status"), switching ? switchState.replaceAll("_", " ") : `ACTIVE: ${active.replaceAll("_", " ")}`, switching ? "warn" : "good");
  text("scope-current", active.replaceAll("_", " "));
  text("scope-execution", active === "XAU_ONLY" ? "XAU_USDT PERPETUAL" : "DYNAMIC CRYPTO UNIVERSE");
  text("scope-allowed", active === "XAU_ONLY" ? "LONG / SHORT / WAIT" : "RANKED CRYPTO ENTRIES");
  text("scope-pending", scope.requested_scope ? `${scope.requested_scope.replaceAll("_", " ")} · ${switchState.replaceAll("_", " ")}` : "NONE");
  $("scope-wide").setAttribute("aria-pressed", String(active === "WIDE_CRYPTO"));
  $("scope-xau").setAttribute("aria-pressed", String(active === "XAU_ONLY"));
  $("scope-wide").disabled = controlPending || switching || active === "WIDE_CRYPTO";
  $("scope-xau").disabled = controlPending || switching || active === "XAU_ONLY";
  $("scope-policy").disabled = controlPending || switching;
  const feedback = $("scope-feedback");
  feedback.textContent = scope.blocking_reason ? `Blocked: ${scope.blocking_reason.replaceAll("_", " ")}` : "";
  feedback.hidden = !scope.blocking_reason;

  const xauPanel = $("xau-panel");
  xauPanel.hidden = active !== "XAU_ONLY";
  $("markets").hidden = active === "XAU_ONLY";
  if (active !== "XAU_ONLY") return;
  const xau = state.strategy?.xau || {};
  const xauMarket = (state.markets || []).find((market) => market.symbol === "XAU_USDT");
  const open = state.open_trade?.symbol === "XAU_USDT" ? state.open_trade : null;
  text("xau-signal", xau.direction || "WAIT");
  $("xau-signal").className = `badge ${["LONG", "SHORT"].includes(xau.direction) ? "paper" : "neutral"}`;
  text("xau-price", Number.isFinite(xauMarket?.last) ? `$${price(xauMarket.last)}` : "N/A");
  text("xau-confidence", Number.isFinite(xau.confidence) ? `${Math.round(xau.confidence * 100)}%` : "0%");
  text("xau-regime", (xau.regime || "UNTRADABLE").replaceAll("_", " "));
  text("xau-volatility", xau.volatility || "UNKNOWN");
  text("xaut-health", xau.feed_health?.XAUT_USDT || "UNAVAILABLE");
  text("paxg-health", xau.feed_health?.PAXG_USDT || "UNAVAILABLE");
  text("xau-position", open ? open.side : "FLAT");
  text("xau-entry-pnl", open ? `$${price(open.entry_price)} / ${metricPercent(open.pnl_pct)}` : "N/A / N/A");
  text("xau-stop-target", open ? `$${price(open.stop_price)} / $${price(open.target_price)}` : "N/A / N/A");
  const xauLeverage = Number.isFinite(state.risk?.xau_leverage) ? `${state.risk.xau_leverage}x` : "N/A";
  const xauRisk = Number.isFinite(state.risk?.xau_risk_per_trade_usdt) ? `$${money.format(state.risk.xau_risk_per_trade_usdt)}` : "N/A";
  text("xau-leverage-risk", `${xauLeverage} / ${xauRisk}`);
  text("xau-reason", `${(xau.reasons || ["WAITING_FOR_DATA"]).join(", ").replaceAll("_", " ")} · confirmation ${xau.confirmation || "UNAVAILABLE"} · contract ${xau.contract_status || "CHECKING"}`);
}

function renderTradingView(tradingview = {}) {
  const value = tradingview.status || "UNAVAILABLE";
  const state = value === "CONNECTED" ? "good" : ["DEGRADED", "STALE"].includes(value) ? "warn" : value === "DISABLED" ? "neutral" : "bad";
  status($("tv-status"), value, state);
  text("tv-symbol", tradingview.symbol || tradingview.expected_symbol || "N/A");
  $("tv-symbol").title = tradingview.symbol || tradingview.expected_symbol || "";
  text("tv-timeframe", tradingview.timeframe || tradingview.expected_timeframe || "N/A");
  const confidence = Number.isFinite(tradingview.confidence) ? `${Math.round(tradingview.confidence * 100)}%` : "N/A";
  text("tv-bias", `${tradingview.bias || "UNAVAILABLE"} / ${confidence}`);
  const secondaryPrice = Number.isFinite(tradingview.price) ? `$${price(tradingview.price)}` : "N/A";
  const divergence = Number.isFinite(tradingview.price_divergence_pct) ? `${signed(tradingview.price_divergence_pct, 4)}%` : "N/A";
  text("tv-price", `${secondaryPrice} / ${divergence}`);
  text("tv-indicators", Number.isFinite(tradingview.indicator_count) ? String(tradingview.indicator_count) : "N/A");
  $("tv-indicators").title = (tradingview.indicators || []).map((item) => `${item.study} · ${item.name}: ${item.value}`).join("\n");
  text("tv-regime", `${tradingview.regime || "UNAVAILABLE"} / ${tradingview.pine_signal || "UNAVAILABLE"}`);
  text("tv-agreement", tradingview.nautilus_agreement || "UNAVAILABLE");
  const latency = Number.isFinite(tradingview.latency_ms) ? `${tradingview.latency_ms} ms` : "N/A";
  const freshness = Number.isFinite(tradingview.freshness_ms) ? `${(tradingview.freshness_ms / 1000).toFixed(1)} s` : "N/A";
  text("tv-timing", `${latency} / ${freshness}`);
  text("tv-detail", tradingview.error || "No execution influence. Gate and Nautilus remain authoritative.");
}

function renderTradeHistory(trades = []) {
  const body = $("trade-rows");
  if (!trades.length) {
    const row = document.createElement("tr");
    const item = document.createElement("td");
    item.colSpan = 11;
    item.className = "empty";
    item.textContent = "No timestamped paper trades in the last 48 hours.";
    row.append(item);
    body.replaceChildren(row);
    return;
  }
  body.replaceChildren(...trades.map((trade) => {
    const row = document.createElement("tr");
    const pnlClass = trade.realized_pnl_usdt >= 0 ? "positive" : "negative";
    cell(row, new Date(trade.closed_at).toLocaleString());
    cell(row, trade.symbol.replace("_", " / "), "symbol");
    cell(row, trade.classification || (trade.reason === "RECOVERY_FLATTEN" ? "OUTAGE HELD" : trade.reason.includes("MANUAL") ? "MANUAL" : "NORMAL"));
    cell(row, trade.side);
    cell(row, `$${price(trade.open_price)}`);
    cell(row, `$${price(trade.close_price)}`);
    const gross = Number.isFinite(trade.gross_price_pnl_usdt) ? trade.gross_price_pnl_usdt : trade.realized_pnl_usdt + trade.fee_usdt;
    cell(row, metricMoney(gross), gross >= 0 ? "positive" : "negative");
    cell(row, `$${money.format(trade.fee_usdt)}`);
    cell(row, metricMoney(trade.realized_pnl_usdt), pnlClass);
    cell(row, `${signed(trade.pnl_pct, 3)}%`, pnlClass);
    cell(row, trade.reason.replaceAll("_", " "));
    return row;
  }));
}

function renderProfitability(profitability = {}, experiments = {}) {
  const filtered = profitability.market_classes?.[analyticsScope];
  const normal = analyticsScope === "ALL" ? profitability.normal || {} : filtered || {};
  const recovery = analyticsScope === "ALL" ? profitability.recovery || {} : {};
  const manual = analyticsScope === "ALL" ? profitability.manual || {} : {};
  const full = analyticsScope === "ALL" ? profitability.full_run || {} : filtered || {};
  text("normal-net", metricMoney(normal.net_pnl_usdt));
  text("recovery-net", metricMoney(recovery.net_pnl_usdt));
  text("manual-net", metricMoney(manual.net_pnl_usdt));
  text("full-net", metricMoney(full.net_pnl_usdt));
  text("gross-pnl", metricMoney(full.gross_price_pnl_usdt));
  text("total-fees", metricMoney(Number.isFinite(full.fees_usdt) ? -full.fees_usdt : NaN));
  text("open-fees", metricMoney(Number.isFinite(profitability.open_position_fees_usdt) ? -profitability.open_position_fees_usdt : NaN));
  const realized = analyticsScope === "ALL" ? profitability.realized_pnl_usdt : full.net_pnl_usdt;
  const unrealized = analyticsScope === "ALL" ? profitability.unrealized_pnl_usdt : full.unrealized_pnl_usdt;
  text("realized-unrealized", `${metricMoney(realized)} / ${metricMoney(unrealized)}`);
  const spread = Number.isFinite(profitability.spread_cost_usdt) ? `$${money.format(profitability.spread_cost_usdt)}` : "N/A";
  const slippage = Number.isFinite(profitability.modeled_slippage_usdt) ? `$${money.format(profitability.modeled_slippage_usdt)}` : "N/A";
  text("spread-slippage", `${spread} / ${slippage}`);
  text("turnover", Number.isFinite(full.turnover_usdt) ? `$${money.format(full.turnover_usdt)}` : "N/A");
  text("calendar-drawdowns", `${metricPercent(profitability.current_session_drawdown_pct)} / ${metricPercent(profitability.current_utc_day_drawdown_pct)} / ${metricPercent(profitability.current_wib_day_drawdown_pct)}`);
  text("run-drawdowns", `${metricPercent(profitability.current_run_start_drawdown_pct)} / ${metricPercent(profitability.current_all_time_high_drawdown_pct)}`);
  text("normal-drawdown", metricPercent(analyticsScope === "ALL" ? profitability.current_normal_strategy_drawdown_pct : full.max_drawdown_pct));
  text("risk-usage", `${metricMoney(Number.isFinite(profitability.current_open_risk_usdt) ? -profitability.current_open_risk_usdt : NaN)} / ${metricMoney(Number.isFinite(profitability.realized_daily_risk_usage_usdt) ? -profitability.realized_daily_risk_usage_usdt : NaN)}`);
  text("experiment-mode", (experiments.mode || "UNCHANGED_BASELINE").replaceAll("_", " "));
  text("exit-mode", (experiments.exit_variant || "BASELINE_FULL_TP").replaceAll("_", " "));
  const blocks = experiments.rejection_counts || {};
  text("experiment-blocks", `${blocks.SIGNAL_RESET_REQUIRED || 0} / ${blocks.COST_TO_EDGE_REJECTED || 0}`);
  text("trade-frequency", Number.isFinite(profitability.trades_per_active_hour) ? profitability.trades_per_active_hour.toFixed(2) : "N/A");
  const evidence = experiments.evidence_status || "INSUFFICIENT EVIDENCE";
  text("evidence-status", evidence);
  $("evidence-status").className = `badge ${evidence === "VALIDATED PAPER CANDIDATE" ? "paper" : "neutral"}`;
}

function renderAma(ama = {}) {
  const state = ama.status || "UNAVAILABLE";
  text("ama-status", state.replaceAll("_", " "));
  $("ama-status").className = `badge ${state === "COLLECTING" ? "paper" : "neutral"}`;
  text("ama-price", Number.isFinite(ama.price) ? `$${price(ama.price)}` : "N/A");
  const kama = ama.kama || {};
  const displayKama = (value) => Number.isFinite(value) ? `$${price(value)}` : "N/A";
  text("ama-values", `${displayKama(kama["10"])} / ${displayKama(kama["20"])} / ${displayKama(kama["50"])}`);
  text("ama-regime", (ama.regime || "UNKNOWN").replaceAll("_", " "));
  text("ama-signals", `${ama.raw_signal || "WAIT"} / ${ama.filtered_signal || "WAIT"}`);
  text("ama-hysteresis", Number.isFinite(ama.hysteresis_bps) ? `${ama.hysteresis_bps.toFixed(2)} bps` : "N/A");
  text("ama-quality", (ama.quality_decision || "WARMING_UP").replaceAll("_", " "));
  const reference = ama.reference || {};
  const dislocation = Number.isFinite(reference.dislocation_bps) ? ` · ${signed(reference.dislocation_bps, 2)} bps` : "";
  text("ama-reference", `${reference.status || "UNAVAILABLE"}${dislocation}`);
  text("ama-evidence", `${ama.evidence_integrity || "UNKNOWN"} · ${ama.observations || 0} observations`);

  const strategies = ama.strategies || {};
  const labels = [
    ["CURRENT_PAPER_BASELINE", "Current PAPER baseline"],
    ["XAU_KAMA20_RAW_V2", "KAMA20 raw"],
    ["XAU_KAMA20_FILTERED_V2", "KAMA20 filtered"],
    ["XAU_FAIR_VALUE_V1", "Fair value"],
    ["XAU_COMBINED_V1", "Combined"],
    ["ENTRY_V3", "Entry V3"],
  ];
  const rows = labels.map(([id, label]) => {
    const metrics = strategies[id] || {};
    const row = document.createElement("tr");
    cell(row, label, "symbol");
    cell(row, (metrics.status || "UNAVAILABLE").replaceAll("_", " "));
    cell(row, String(metrics.trades || 0));
    cell(row, metricMoney(metrics.net_pnl_usdt));
    cell(row, Number.isFinite(metrics.profit_factor) ? metrics.profit_factor.toFixed(2) : "N/A");
    cell(row, Number.isFinite(metrics.max_drawdown_usdt) ? `$${money.format(metrics.max_drawdown_usdt)}` : "N/A");
    cell(row, (metrics.sample_status || "INSUFFICIENT_EVIDENCE").replaceAll("_", " "));
    return row;
  });
  $("ama-comparison-rows").replaceChildren(...rows);
  const comparison = ama.comparison || {};
  text("ama-complexity", `Complexity alpha: ${(comparison.complexity_alpha || "UNPROVEN").replaceAll("_", " ")} · ${comparison.status || "INSUFFICIENT EVIDENCE"} · no automatic promotion.`);
  text("ama-strategy-status", `${state} · EXECUTION DISABLED`);
  text("ama-strategy-detail", `${ama.filtered_signal || "WAIT"} after quality gate · ${ama.counterfactual_pending || 0} pending counterfactuals · no order path.`);
}

function emptyRow(columnCount, message) {
  const row = document.createElement("tr");
  const item = document.createElement("td");
  item.colSpan = columnCount;
  item.className = "empty";
  item.textContent = message;
  row.append(item);
  return row;
}

function renderResearch(research = {}) {
  const ready = research.status === "READY" && research.dataset;
  const badge = $("research-status");
  if (!ready) {
    badge.textContent = research.status || "UNAVAILABLE";
    badge.className = `badge ${research.status === "ERROR" ? "bad" : "neutral"}`;
    text("research-detail", research.reason || "No frozen research report has loaded.");
    text("research-dataset", "N/A");
    text("research-dataset-hash", "N/A");
    text("research-commit", "N/A");
    text("research-config", "N/A");
    text("research-profile", "N/A");
    text("research-experiments", "N/A");
    $("research-strategy-rows").replaceChildren(emptyRow(17, "Generate and verify a frozen replay before comparing strategies."));
    $("research-universe-rows").replaceChildren(emptyRow(7, "No frozen universe has loaded."));
    $("research-candidate-rows").replaceChildren(emptyRow(11, "No cross-sectional candidates have loaded."));
    $("research-overfit-rows").replaceChildren(emptyRow(9, "No overfitting audit has loaded."));
    $("research-funnel-rows").replaceChildren(emptyRow(6, "No candidate decision ledger has loaded."));
    $("research-no-trade-rows").replaceChildren(emptyRow(6, "No resolved candidate outcomes have loaded."));
    $("research-edge-rows").replaceChildren(emptyRow(10, "No versioned execution profile has loaded."));
    $("research-lifecycle-rows").replaceChildren(emptyRow(4, "No lifecycle registry has loaded."));
    return;
  }

  const eligible = research.paper_eligible_strategies || [];
  badge.textContent = eligible.length ? `${eligible.length} PAPER ELIGIBLE` : "NO STRATEGY ELIGIBLE";
  badge.className = `badge ${eligible.length ? "paper" : "warn"}`;
  text("research-detail", eligible.length ? "Eligibility is evidence-only. Activation still requires operator approval." : "Every evaluated strategy remains blocked from PAPER activation.");
  const identity = research.experiment_identity || {};
  const short = (value) => typeof value === "string" && value.length > 16 ? value.slice(0, 16) : value || "N/A";
  text("research-dataset", identity.dataset_id || research.dataset.dataset_id || "N/A");
  text("research-dataset-hash", short(identity.dataset_hash));
  $("research-dataset-hash").title = identity.dataset_hash || "";
  text("research-commit", short(identity.git_commit));
  $("research-commit").title = identity.git_commit || "";
  text("research-config", short(identity.config_hash));
  $("research-config").title = identity.config_hash || "";
  text("research-profile", identity.execution_profile || "N/A");
  const experimentIds = identity.experiment_ids || [];
  text("research-experiments", experimentIds.length ? experimentIds.map(short).join(", ") : "N/A");
  $("research-experiments").title = experimentIds.join("\n");

  const unsupported = (research.unsupported_strategies || []).map((row) => ({
    strategy: row.strategy_id,
    version: row.strategy_version,
    lifecycle_state: row.lifecycle_state,
    sample: "N/A",
    gross_expectancy_bps: null,
    net_expectancy_bps: null,
    profit_factor: null,
    maximum_drawdown_bps: null,
    test_result: "N/A",
    final_holdout_result: "N/A",
    stress_result: null,
    parameter_stability: "N/A",
    promotion_eligible: false,
    reason_blocked: [row.status, row.reason],
  }));
  const tournament = research.strategy_tournament || [...(research.strategy_lab || []), ...unsupported];
  const strategyRows = tournament.map((metrics) => {
    const row = document.createElement("tr");
    cell(row, metrics.strategy || "UNKNOWN", "symbol");
    cell(row, metrics.version || "N/A");
    cell(row, metrics.lifecycle_state || "EXPERIMENTAL");
    cell(row, metrics.comparable || "N/A");
    cell(row, String(metrics.accepted_trades ?? metrics.sample ?? "N/A"));
    cell(row, metricBps(metrics.gross_expectancy_bps));
    cell(row, metricBps(metrics.net_expectancy_bps), Number(metrics.net_expectancy_bps) > 0 ? "positive" : "negative");
    cell(row, Number.isFinite(metrics.profit_factor) ? metrics.profit_factor.toFixed(2) : "N/A");
    cell(row, metricBps(metrics.maximum_drawdown_bps));
    cell(row, Number.isFinite(metrics.edge_cost_ratio) ? metrics.edge_cost_ratio.toFixed(2) : "N/A");
    cell(row, metricBps(metrics.validation_result));
    cell(row, metricBps(metrics.test_result));
    cell(row, metricBps(metrics.final_holdout_result));
    cell(row, metricBps(metrics.stress_result?.net_expectancy_bps));
    cell(row, (metrics.parameter_stability || "N/A").replaceAll("_", " "));
    cell(row, metrics.promotion_eligible ? "YES" : "NO", metrics.promotion_eligible ? "positive" : "negative");
    cell(row, (metrics.reason_blocked || []).join(", ").replaceAll("_", " ") || "None");
    return row;
  });
  $("research-strategy-rows").replaceChildren(...strategyRows);

  const universe = research.universe || {};
  const universeRows = [...(universe.selected || []), ...(universe.rejected || []).slice(0, 30)].map((market) => {
    const row = document.createElement("tr");
    cell(row, market.symbol || "UNKNOWN", "symbol");
    cell(row, market.tradability || "UNKNOWN");
    cell(row, Number.isFinite(market.snapshot_quote_volume_usdt) ? `${compact.format(market.snapshot_quote_volume_usdt)} USDT` : "N/A");
    cell(row, metricBps(market.snapshot_spread_bps));
    cell(row, Number.isFinite(market.listing_age_days) ? `${market.listing_age_days.toFixed(0)}d` : "N/A");
    cell(row, Number.isFinite(Number(market.funding_rate)) ? metricPercent(Number(market.funding_rate) * 100) : "N/A");
    cell(row, (market.rejection_reasons || []).join(", ").replaceAll("_", " ") || "Eligible");
    return row;
  });
  $("research-universe-rows").replaceChildren(...(universeRows.length ? universeRows : [emptyRow(7, "No frozen universe has loaded.")]));

  const candidateRows = (research.top_candidates || []).map((candidate) => {
    const row = document.createElement("tr");
    cell(row, candidate.symbol || "UNKNOWN", "symbol");
    cell(row, candidate.direction || "WAIT");
    cell(row, Number.isFinite(candidate.normalized_momentum) ? candidate.normalized_momentum.toFixed(3) : "N/A");
    cell(row, Number.isFinite(candidate.percentile) ? metricPercent(candidate.percentile * 100) : "N/A");
    cell(row, metricBps(candidate.breakout_distance_bps));
    cell(row, Number.isFinite(candidate.volatility_ratio) ? `${candidate.volatility_ratio.toFixed(2)}x` : "N/A");
    cell(row, Number.isFinite(candidate.volume_ratio) ? `${candidate.volume_ratio.toFixed(2)}x` : "N/A");
    cell(row, metricBps(candidate.expected_move_bps));
    cell(row, metricBps(candidate.execution_cost_bps));
    cell(row, Number.isFinite(candidate.edge_cost_ratio) ? candidate.edge_cost_ratio.toFixed(2) : "N/A");
    cell(row, candidate.decision === "ACCEPTED" ? "ACCEPTED" : (candidate.first_rejection_reason || "REJECTED").replaceAll("_", " "));
    return row;
  });
  $("research-candidate-rows").replaceChildren(...(candidateRows.length ? candidateRows : [emptyRow(11, "No cross-sectional candidates have loaded.")]));

  const audit = research.overfitting_audit;
  if (audit) {
    const combinedCpcv = research.cpcv?.CROSS_SECTIONAL_BREAKOUT_V1 || {};
    const row = document.createElement("tr");
    cell(row, String(audit.experiments_run ?? "N/A"));
    cell(row, String(audit.families_tested ?? "N/A"));
    cell(row, String(audit.parameter_variants_tested ?? "N/A"));
    cell(row, String(audit.holdout_access_count ?? "N/A"));
    cell(row, String(combinedCpcv.number_of_paths ?? "N/A"));
    cell(row, Number.isFinite(combinedCpcv.positive_path_fraction) ? metricPercent(combinedCpcv.positive_path_fraction * 100) : "N/A");
    cell(row, audit.deflated_sharpe_ratio?.status || "UNAVAILABLE");
    cell(row, audit.probability_of_backtest_overfitting?.status || "UNAVAILABLE");
    cell(row, audit.synthetic_null?.status || "UNAVAILABLE");
    $("research-overfit-rows").replaceChildren(row);
  } else {
    $("research-overfit-rows").replaceChildren(emptyRow(9, "No overfitting audit has loaded."));
  }

  const funnels = research.candidate_funnels || {};
  const funnelRows = Object.entries(funnels).map(([name, funnel]) => {
    const row = document.createElement("tr");
    const binding = funnel?.binding_gates?.[0] || {};
    cell(row, name.replaceAll("_", " "), "symbol");
    cell(row, String(funnel?.generated ?? "N/A"));
    cell(row, String(funnel?.accepted ?? "N/A"));
    cell(row, String(funnel?.rejected ?? "N/A"));
    cell(row, (binding.gate || "N/A").replaceAll("_", " "));
    cell(row, String(binding.reject_count ?? "N/A"));
    return row;
  });
  $("research-funnel-rows").replaceChildren(...(funnelRows.length ? funnelRows : [emptyRow(6, "No candidate funnel has loaded.")]));

  const noTrade = research.no_trade_value || {};
  const noTradeRows = Object.entries(noTrade).map(([name, value]) => {
    const row = document.createElement("tr");
    const funnel = funnels[name] || {};
    cell(row, name.replaceAll("_", " "), "symbol");
    cell(row, String(value?.rejected_resolved_count ?? value?.rejected ?? funnel.rejected ?? "N/A"));
    cell(row, String(value?.classifications?.AVOIDED_LOSS ?? value?.avoided_losses ?? "N/A"));
    cell(row, String(value?.classifications?.MISSED_OPPORTUNITY ?? value?.missed_profitable_trades ?? "N/A"));
    cell(row, metricBps(value?.net_filter_benefit_bps));
    cell(row, duration(value?.time_in_no_trade_seconds));
    return row;
  });
  $("research-no-trade-rows").replaceChildren(...(noTradeRows.length ? noTradeRows : [emptyRow(6, "No resolved outcomes have loaded.")]));

  const edgeRows = Object.entries(research.execution_edge || {}).map(([name, edge]) => {
    const row = document.createElement("tr");
    cell(row, name, "symbol");
    cell(row, metricBps(edge.gross_move_bps));
    cell(row, metricBps(edge.spread_cost_bps));
    cell(row, metricBps(edge.fee_cost_bps));
    cell(row, metricBps(edge.slippage_cost_bps));
    cell(row, metricBps(edge.latency_cost_bps));
    cell(row, metricBps(edge.adverse_selection_bps));
    cell(row, metricBps(edge.funding_cost_bps));
    cell(row, metricBps(edge.impact_bps));
    cell(row, metricBps(edge.net_edge_bps), Number(edge.net_edge_bps) > 0 ? "positive" : "negative");
    return row;
  });
  $("research-edge-rows").replaceChildren(...(edgeRows.length ? edgeRows : [emptyRow(10, "No execution decomposition has loaded.")]));

  const lifecycleRows = Object.values(research.lifecycle || {}).map((item) => {
    const row = document.createElement("tr");
    cell(row, item.strategy_id || "UNKNOWN", "symbol");
    cell(row, item.strategy_version || "N/A");
    cell(row, item.state || "UNKNOWN");
    cell(row, (item.reason || "N/A").replaceAll("_", " "));
    return row;
  });
  $("research-lifecycle-rows").replaceChildren(...(lifecycleRows.length ? lifecycleRows : [emptyRow(4, "No lifecycle registry has loaded.")]));
}

function render(state) {
  latestState = state;
  const accounting = state.accounting || { state: "INVALID", reason: "Accounting diagnostics unavailable." };
  const accountingValid = accounting.state === "VALID";
  const accountingBanner = $("accounting-banner");
  accountingBanner.className = `integrity-banner ${accountingValid ? "valid" : "invalid"}`;
  text("accounting-state", `ACCOUNTING ${accounting.state || "INVALID"}`);
  text("accounting-detail", accountingValid ? `Checkpoint ${accounting.checkpoint_sequence} and event ${accounting.last_event_sequence} reconcile at ${accounting.tolerance_usdt} USDT tolerance.` : accounting.reason || "Reconciliation failed; Resume is disabled.");
  const openTradeTicker = $("open-trade-ticker");
  const openTrade = state.open_trade;
  openTradeTicker.textContent = openTrade ? `OPEN PAPER TRADE · ${openTrade.symbol.replace("_", " / ")} · ${openTrade.side} · ENTRY $${price(openTrade.entry_price)} · NOW ${Number.isFinite(openTrade.current_price) ? `$${price(openTrade.current_price)}` : "N/A"}` : "OPEN PAPER TRADE";
  openTradeTicker.hidden = !openTrade;
  status($("engine-status"), "ENGINE SIM READY", state.engine.risk_engine_enabled ? "good" : "bad");
  status($("data-status"), `DATA ${state.data.status}`, state.data.status === "LIVE" ? "good" : "bad");
  text("latency", state.data.latency_ms === null ? "N/A ms" : `${state.data.latency_ms} ms`);
  text("trading-state", state.trading_state);
  text("state-detail", state.trading_state === "ACTIVE" ? "Paper strategy and market screening are active." : accountingValid ? "New paper entries are blocked." : "New entries are blocked by accounting integrity.");
  const riskState = state.risk?.state || "UNKNOWN";
  const riskDay = state.risk?.risk_day_utc;
  haltTiming = state.trading_state === "HALTED" && riskState !== "OK" ? {
    riskState,
    reviewAt: ["DAILY_LOSS", "MAX_DRAWDOWN"].includes(riskState) && /^\d{4}-\d{2}-\d{2}$/.test(riskDay || "")
      ? Date.parse(`${riskDay}T00:00:00Z`) + 86_400_000
      : NaN,
  } : null;
  renderHaltTiming();
  text("equity", `$${money.format(state.portfolio.equity_usdt)}`);
  text("pnl", `$${money.format(state.portfolio.daily_pnl_usdt)}`);
  text("drawdown", `${state.portfolio.drawdown_pct.toFixed(2)}%`);
  text("trades", String(state.portfolio.trades_today));
  text("positions", String(state.portfolio.open_positions));
  text("active-symbols", String(state.active_symbols));
  text("accounting-health", accounting.state || "INVALID");
  text("risk-health", state.engine.risk_engine_enabled ? "ACTIVE" : "FAILED");
  text("risk-state", (state.risk?.state || "UNKNOWN").replaceAll("_", " "));
  text("execution-model", `${state.execution_model?.state || "UNKNOWN"} · ${state.execution_model?.queue_model || "UNKNOWN"}`);
  text("data-source", state.data.source);
  text("data-age", state.data.age_seconds === null ? "NO DATA" : `${state.data.age_seconds.toFixed(1)} s`);
  text("last-event", state.data.last_event_utc ? new Date(state.data.last_event_utc).toLocaleTimeString() : "N/A");
  text("orders-positions", `${state.orders} / ${state.portfolio.open_positions}`);
  const runId = state.run?.run_id || "N/A";
  const sessionId = state.run?.session_id || "N/A";
  text("run-session", `${runId.slice(0, 8)} / ${sessionId.slice(0, 8)}`);
  $("run-session").title = `${runId} / ${sessionId}`;
  const gitCommit = state.run?.git_commit || "UNKNOWN";
  const configHash = state.run?.config_hash || "UNKNOWN";
  text("build-config", `${gitCommit.slice(0, 8)} / ${configHash.slice(0, 8)}`);
  $("build-config").title = `${gitCommit} / ${configHash}`;
  text("checkpoint-event", `${accounting.checkpoint_sequence ?? "N/A"} / ${accounting.last_event_sequence ?? "N/A"}`);
  const recoveryTiming = state.recovery_timing || {};
  const outage = Number.isFinite(recoveryTiming.outage_duration_ms) ? `${(recoveryTiming.outage_duration_ms / 1000).toFixed(1)} s` : "N/A";
  text("restart-outage", `${recoveryTiming.restart_detected_at_utc ? new Date(recoveryTiming.restart_detected_at_utc).toLocaleString() : "N/A"} / ${outage}`);
  text("recovery-action", (recoveryTiming.recovery_action || "NONE").replaceAll("_", " "));
  const freeBytes = state.storage?.free_disk_bytes;
  text("free-disk", Number.isFinite(freeBytes) ? `${(freeBytes / 1_000_000_000).toFixed(1)} GB · ${state.storage.free_disk_percent.toFixed(1)}%` : "N/A");
  text("strategy-status", state.strategy?.armed ? "PAPER ACTIVE" : "PAPER PAUSED");
  text("strategy-detail", "Current PAPER execution baseline. Orders remain behind scope, risk, freshness, accounting, and final entry guards.");
  const v3 = state.entry_v3 || {};
  text("entry-v3-status", `${v3.strategy_status || "SHADOW"} · ${v3.status || "DISABLED"}`);
  const latest = v3.latest_candidate || {};
  const candidateState = latest.decision ? `${latest.decision} ${latest.direction || ""}` : "no candidate yet";
  text("entry-v3-detail", `${v3.candidate_count || 0} candidates, ${v3.accepted_count || 0} accepted, ${v3.rejected_count || 0} rejected · ${candidateState} · orders disabled.`);
  text("strategy-badge", v3.status === "LIVE" ? "V3 SHADOW" : "V3 CHECKING");
  $("strategy-badge").className = `badge ${v3.status === "LIVE" ? "paper" : "neutral"}`;
  $("pause-button").disabled = !state.controls.pause_allowed;
  $("resume-button").disabled = !state.controls.resume_allowed;
  $("flatten-button").disabled = !state.controls.flatten_allowed;
  $("new-run-button").disabled = !state.controls.new_paper_run_allowed;
  $("restart-button").disabled = controlPending;
  $("analyze-button").disabled = controlPending;
  if (state.recovery) {
    const recovery = state.recovery;
    text("recovery-status", `ANALYSIS ${recovery.status} · ${new Date(recovery.checked_at_utc).toLocaleTimeString()}`);
    text("recovery-detail", recovery.detail);
    text("recovery-trigger", `Detected: ${recovery.trigger}`);
    $("recovery-trigger").hidden = false;
    $("recovery-checks").replaceChildren(...[...recovery.checks, ...recovery.actions].map((message) => {
      const item = document.createElement("li");
      item.textContent = message;
      return item;
    }));
    text("recovery-next", recovery.next_action);
  }
  if (controlPending) for (const button of document.querySelectorAll("button")) button.disabled = true;
  $("connection-banner").hidden = true;
  renderTradingView(state.tradingview);
  renderMarketScope(state);
  renderMarkets(state.markets);
  renderTradeHistory(state.trade_history);
  renderProfitability(state.profitability, state.experiments);
  renderAma(state.ama_control || state.strategy?.ama_control || {});
  renderResearch(state.research || {});

  const alerts = state.alerts.map((message) => {
    const alert = document.createElement("p");
    alert.className = "alert";
    alert.textContent = message;
    return alert;
  });
  $("alerts").replaceChildren(...alerts);
}

function disconnected() {
  $("open-trade-ticker").hidden = true;
  status($("engine-status"), "ENGINE UNKNOWN", "bad");
  status($("data-status"), "DATA UNKNOWN", "bad");
  text("trading-state", "UNKNOWN");
  text("state-detail", "Backend state unavailable. Controls are disabled.");
  haltTiming = null;
  renderHaltTiming();
  $("accounting-banner").className = "integrity-banner invalid";
  text("accounting-state", "ACCOUNTING UNKNOWN");
  text("accounting-detail", "Backend unavailable; accounting cannot be verified and Resume is disabled.");
  $("pause-button").disabled = true;
  $("resume-button").disabled = true;
  $("flatten-button").disabled = true;
  $("new-run-button").disabled = true;
  $("restart-button").disabled = true;
  $("analyze-button").disabled = true;
  $("scope-wide").disabled = true;
  $("scope-xau").disabled = true;
  $("scope-policy").disabled = true;
  $("xau-panel").hidden = true;
  $("connection-banner").hidden = false;
  renderTradingView({ status: "UNAVAILABLE", error: "Backend state unavailable." });
  renderTradeHistory([]);
  renderProfitability({}, {});
  renderAma({ status: "UNAVAILABLE", evidence_integrity: "UNKNOWN" });
  renderResearch({ status: "ERROR", reason: "Backend unavailable; research evidence cannot be verified." });
}

async function switchScope(target) {
  if (controlPending) return;
  const policy = $("scope-policy").value;
  const confirmFlatten = policy === "FLATTEN_AND_SWITCH";
  if (confirmFlatten && !window.confirm("Flatten the current PAPER position, reconcile accounting, and switch market scope?")) return;
  controlPending = true;
  $("scope-feedback").hidden = true;
  status($("scope-status"), "SWITCHING", "warn");
  for (const button of document.querySelectorAll("button")) button.disabled = true;
  try {
    const response = await fetch("/api/market-scope", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: target, switch_policy: policy, confirm_flatten: confirmFlatten }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  } catch (error) {
    text("scope-feedback", `Switch failed: ${error.message}.`);
    $("scope-feedback").hidden = false;
  } finally {
    controlPending = false;
    await refresh();
  }
}

async function refresh() {
  if (controlPending) return;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch {
    disconnected();
  }
}

async function control(action, payload = {}) {
  if (controlPending) return;
  controlPending = true;
  $("control-feedback").hidden = true;
  if (action === "analyze-repair") {
    text("analyze-button", "ANALYZING…");
    $("recovery-panel").setAttribute("aria-busy", "true");
  }
  for (const button of document.querySelectorAll("button")) button.disabled = true;
  try {
    const response = await fetch(`/api/control/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    controlPending = false;
    render(result);
  } catch (error) {
    controlPending = false;
    text("control-feedback", `Action failed: ${error.message}. Check the current state before retrying.`);
    $("control-feedback").hidden = false;
    await refresh();
  } finally {
    controlPending = false;
    text("analyze-button", "ANALYZE & AUTO-FIX");
    $("recovery-panel").removeAttribute("aria-busy");
  }
}

$("pause-button").addEventListener("click", () => control("pause"));
$("resume-button").addEventListener("click", () => control("resume"));
$("flatten-button").addEventListener("click", () => control("flatten"));
$("restart-button").addEventListener("click", () => control("restart-feed"));
$("analyze-button").addEventListener("click", () => control("analyze-repair"));
$("new-run-button").addEventListener("click", () => {
  if (window.confirm("Archive this halted run and start a new $300 PAPER experiment?")) {
    control("new-paper-run", { confirm_new_run: true });
  }
});
$("scope-wide").addEventListener("click", () => switchScope("WIDE_CRYPTO"));
$("scope-xau").addEventListener("click", () => switchScope("XAU_ONLY"));
$("analytics-scope").addEventListener("change", (event) => {
  analyticsScope = event.target.value;
  if (latestState) renderProfitability(latestState.profitability, latestState.experiments);
});
refresh();
setInterval(refresh, 5000);
setInterval(renderHaltTiming, 1000);
