"use strict";

const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
let controlPending = false;
let haltTiming = null;

function text(id, value) { $(id).textContent = value; }
function signed(value, digits = 2) { return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`; }
function price(value) {
  const maximumFractionDigits = value >= 1000 ? 2 : value >= 1 ? 4 : value >= 0.01 ? 6 : 8;
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits });
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
  if (haltTiming.riskState !== "DAILY_LOSS") {
    element.textContent = haltTiming.riskState === "MAX_DRAWDOWN"
      ? "NO COUNTDOWN · MAX DRAWDOWN DOES NOT EXPIRE · MANUAL REVIEW REQUIRED"
      : `NO AUTOMATIC LIFT · ${haltTiming.riskState.replaceAll("_", " ")} · MANUAL REVIEW REQUIRED`;
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

function renderTradingView(tradingview = {}) {
  const value = tradingview.status || "UNAVAILABLE";
  const state = value === "CONNECTED" ? "good" : ["DEGRADED", "STALE"].includes(value) ? "warn" : value === "DISABLED" ? "neutral" : "bad";
  status($("tv-status"), value, state);
  text("tv-symbol", tradingview.symbol || tradingview.expected_symbol || "—");
  $("tv-symbol").title = tradingview.symbol || tradingview.expected_symbol || "";
  text("tv-timeframe", tradingview.timeframe || tradingview.expected_timeframe || "—");
  const confidence = Number.isFinite(tradingview.confidence) ? `${Math.round(tradingview.confidence * 100)}%` : "—";
  text("tv-bias", `${tradingview.bias || "UNAVAILABLE"} / ${confidence}`);
  const secondaryPrice = Number.isFinite(tradingview.price) ? `$${price(tradingview.price)}` : "—";
  const divergence = Number.isFinite(tradingview.price_divergence_pct) ? `${signed(tradingview.price_divergence_pct, 4)}%` : "—";
  text("tv-price", `${secondaryPrice} / ${divergence}`);
  text("tv-indicators", Number.isFinite(tradingview.indicator_count) ? String(tradingview.indicator_count) : "—");
  $("tv-indicators").title = (tradingview.indicators || []).map((item) => `${item.study} · ${item.name}: ${item.value}`).join("\n");
  text("tv-regime", `${tradingview.regime || "UNAVAILABLE"} / ${tradingview.pine_signal || "UNAVAILABLE"}`);
  text("tv-agreement", tradingview.nautilus_agreement || "UNAVAILABLE");
  const latency = Number.isFinite(tradingview.latency_ms) ? `${tradingview.latency_ms} ms` : "—";
  const freshness = Number.isFinite(tradingview.freshness_ms) ? `${(tradingview.freshness_ms / 1000).toFixed(1)} s` : "—";
  text("tv-timing", `${latency} / ${freshness}`);
  text("tv-detail", tradingview.error || "No execution influence. Gate and Nautilus remain authoritative.");
}

function renderTradeHistory(trades = []) {
  const body = $("trade-rows");
  if (!trades.length) {
    const row = document.createElement("tr");
    const item = document.createElement("td");
    item.colSpan = 9;
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
    cell(row, trade.side);
    cell(row, `$${price(trade.open_price)}`);
    cell(row, `$${price(trade.close_price)}`);
    cell(row, `${trade.realized_pnl_usdt >= 0 ? "+" : "-"}$${money.format(Math.abs(trade.realized_pnl_usdt))}`, pnlClass);
    cell(row, `${signed(trade.pnl_pct, 3)}%`, pnlClass);
    cell(row, `$${money.format(trade.fee_usdt)}`);
    cell(row, trade.reason.replaceAll("_", " "));
    return row;
  }));
}

function render(state) {
  const accounting = state.accounting || { state: "INVALID", reason: "Accounting diagnostics unavailable." };
  const accountingValid = accounting.state === "VALID";
  const accountingBanner = $("accounting-banner");
  accountingBanner.className = `integrity-banner ${accountingValid ? "valid" : "invalid"}`;
  text("accounting-state", `ACCOUNTING ${accounting.state || "INVALID"}`);
  text("accounting-detail", accountingValid ? `Checkpoint ${accounting.checkpoint_sequence} and event ${accounting.last_event_sequence} reconcile at ${accounting.tolerance_usdt} USDT tolerance.` : accounting.reason || "Reconciliation failed; Resume is disabled.");
  const openTradeTicker = $("open-trade-ticker");
  const openTrade = state.open_trade;
  openTradeTicker.textContent = openTrade ? `OPEN PAPER TRADE · ${openTrade.symbol.replace("_", " / ")} · ${openTrade.side} · ENTRY $${price(openTrade.entry_price)} · NOW ${Number.isFinite(openTrade.current_price) ? `$${price(openTrade.current_price)}` : "—"}` : "OPEN PAPER TRADE —";
  openTradeTicker.hidden = !openTrade;
  status($("engine-status"), "ENGINE SIM READY", state.engine.risk_engine_enabled ? "good" : "bad");
  status($("data-status"), `DATA ${state.data.status}`, state.data.status === "LIVE" ? "good" : "bad");
  text("latency", state.data.latency_ms === null ? "— ms" : `${state.data.latency_ms} ms`);
  text("trading-state", state.trading_state);
  text("state-detail", state.trading_state === "ACTIVE" ? "Paper strategy and market screening are active." : accountingValid ? "New paper entries are blocked." : "New entries are blocked by accounting integrity.");
  const riskState = state.risk?.state || "UNKNOWN";
  const riskDay = state.risk?.risk_day_utc;
  haltTiming = state.trading_state === "HALTED" && riskState !== "OK" ? {
    riskState,
    reviewAt: riskState === "DAILY_LOSS" && /^\d{4}-\d{2}-\d{2}$/.test(riskDay || "")
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
  text("last-event", state.data.last_event_utc ? new Date(state.data.last_event_utc).toLocaleTimeString() : "—");
  text("orders-positions", `${state.orders} / ${state.portfolio.open_positions}`);
  const runId = state.run?.run_id || "—";
  const sessionId = state.run?.session_id || "—";
  text("run-session", `${runId.slice(0, 8)} / ${sessionId.slice(0, 8)}`);
  $("run-session").title = `${runId} / ${sessionId}`;
  const gitCommit = state.run?.git_commit || "UNKNOWN";
  const configHash = state.run?.config_hash || "UNKNOWN";
  text("build-config", `${gitCommit.slice(0, 8)} / ${configHash.slice(0, 8)}`);
  $("build-config").title = `${gitCommit} / ${configHash}`;
  text("checkpoint-event", `${accounting.checkpoint_sequence ?? "—"} / ${accounting.last_event_sequence ?? "—"}`);
  const freeBytes = state.storage?.free_disk_bytes;
  text("free-disk", Number.isFinite(freeBytes) ? `${(freeBytes / 1_000_000_000).toFixed(1)} GB · ${state.storage.free_disk_percent.toFixed(1)}%` : "—");
  text("strategy-status", state.strategy.status.replaceAll("_", " "));
  text("strategy-detail", `${state.strategy.monitored_symbols || 0} pairs monitored · 1 execution slot · leader ${state.strategy.symbol.replace("_", " / ")} · confidence ${Math.round((state.strategy.confidence || 0) * 100)}% · net ${signed(state.strategy.expected_net_bps, 1)} bp`);
  text("strategy-badge", state.strategy.armed ? "ARMED" : "NOT ARMED");
  $("strategy-badge").className = `badge ${state.strategy.armed ? "paper" : "neutral"}`;
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
  renderMarkets(state.markets);
  renderTradeHistory(state.trade_history);

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
  $("connection-banner").hidden = false;
  renderTradingView({ status: "UNAVAILABLE", error: "Backend state unavailable." });
  renderTradeHistory([]);
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
refresh();
setInterval(refresh, 5000);
setInterval(renderHaltTiming, 1000);
