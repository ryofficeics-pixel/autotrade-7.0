from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.request import Request, urlopen

from autotrade.config import Settings
from autotrade.entry_v3 import LiveEntryV3Shadow
from autotrade.integrity import create_new_run
from autotrade.market_scope import (
    MarketScope,
    MarketScopeController,
    SwitchPolicy,
    SwitchState,
)
from autotrade.paper import PaperTrader
from autotrade.runtime import RuntimeReport, run_paper_smoke
from autotrade.tradingview import TradingViewMonitor, build_tradingview_monitor

GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
GATE_CONTRACTS_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
MAX_RESPONSE_BYTES = 2_000_000
STATIC_DIR = Path(__file__).resolve().parents[1] / "dashboard"
XAU_SCOPE_SYMBOLS = {"XAU_USDT", "XAUT_USDT", "PAXG_USDT"}


def _decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def rank_tickers(
    payload: object,
    settings: Settings,
    required_symbols: tuple[str, ...] = (),
    price_increments: dict[str, str] | None = None,
    contract_metadata: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("Gate tickers response is not a list")

    eligible: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        contract = str(item.get("contract", ""))
        metadata = (contract_metadata or {}).get(contract, {})
        price_increment = price_increments.get(contract) if price_increments is not None else None
        last = _decimal(item.get("last"))
        bid = _decimal(item.get("highest_bid"))
        ask = _decimal(item.get("lowest_ask"))
        change = _decimal(item.get("change_percentage"))
        volume = _decimal(item.get("volume_24h_quote"))
        funding = _decimal(item.get("funding_rate"))
        if (
            not contract.endswith("_USDT")
            or last is None
            or bid is None
            or ask is None
            or change is None
            or volume is None
            or funding is None
            or min(last, bid, ask) <= 0
            or ask < bid
            or volume < 0
            or (price_increments is not None and price_increment is None)
        ):
            continue

        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000
        rejection = None
        if volume < settings.minimum_quote_volume:
            rejection = "LOW VOLUME"
        elif spread_bps > settings.maximum_spread_bps:
            rejection = "WIDE SPREAD"

        # ponytail: transparent screening heuristic, replace with validated microstructure scores.
        volatility_component = min(abs(float(change)), 10.0) * 4
        liquidity_component = max(0.0, math.log10(max(float(volume), 1.0)) - 5) * 12
        score = max(
            0.0,
            min(
                100.0,
                volatility_component + liquidity_component - float(spread_bps) * 2,
            ),
        )
        market = {
            "symbol": contract,
            "last": float(last),
            "change_pct": float(change),
            "bid": float(bid),
            "ask": float(ask),
            "spread_bps": round(float(spread_bps), 3),
            "volume_quote": float(volume),
            "funding_rate": float(funding),
            "price_increment": price_increment,
            "quantity_increment": metadata.get("quantity_increment"),
            "minimum_quantity": metadata.get("minimum_quantity"),
            "contract_enabled": metadata.get("contract_enabled"),
            "leverage_min": metadata.get("leverage_min"),
            "leverage_max": metadata.get("leverage_max"),
            "contract_value": metadata.get("contract_value"),
            "funding_interval_seconds": metadata.get("funding_interval_seconds"),
            "funding_next_apply": metadata.get("funding_next_apply"),
            "margin_mode": "PAPER_SIMULATION",
            "market_class": "xau" if contract in XAU_SCOPE_SYMBOLS else "crypto",
            "screen_score": round(score, 1),
            "selected": rejection is None and contract not in XAU_SCOPE_SYMBOLS,
            "rejection": (
                rejection
                or ("XAU PROFILE" if contract == "XAU_USDT" else "REFERENCE")
                if contract in XAU_SCOPE_SYMBOLS
                else rejection
            ),
        }
        (eligible if rejection is None else rejected).append(market)

    eligible.sort(key=lambda market: float(str(market["screen_score"])), reverse=True)
    required = set(required_symbols)
    rejected.sort(
        key=lambda market: (
            market["symbol"] in required,
            float(str(market["volume_quote"])),
        ),
        reverse=True,
    )
    required_crypto = required - XAU_SCOPE_SYMBOLS
    selected = [market for market in eligible if market["symbol"] in required_crypto]
    selected.extend(
        market
        for market in eligible
        if market["symbol"] not in required and market["symbol"] not in XAU_SCOPE_SYMBOLS
    )
    selected = selected[: settings.active_symbols]
    visible = selected + rejected[: max(0, 12 - len(selected))]
    visible_symbols = {item["symbol"] for item in visible}
    visible.extend(
        market
        for market in (*eligible, *rejected)
        if market["symbol"] in required and market["symbol"] not in visible_symbols
    )
    return visible


def fetch_gate_tickers() -> object:
    return _fetch_gate_json(GATE_TICKERS_URL)


def fetch_gate_price_increments() -> dict[str, str]:
    payload = _fetch_gate_json(GATE_CONTRACTS_URL)
    if not isinstance(payload, list):
        raise RuntimeError("Gate contracts response is not a list")
    increments: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        contract = str(item.get("name", ""))
        increment = _decimal(item.get("order_price_round"))
        if contract.endswith("_USDT") and increment is not None and increment > 0:
            increments[contract] = format(increment, "f")
    if not increments:
        raise RuntimeError("Gate contract price metadata is empty")
    return increments


def fetch_gate_contracts() -> dict[str, dict[str, object]]:
    payload = _fetch_gate_json(GATE_CONTRACTS_URL)
    if not isinstance(payload, list):
        raise RuntimeError("Gate contracts response is not a list")
    contracts: dict[str, dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        contract = str(item.get("name", ""))
        price_increment = _decimal(item.get("order_price_round"))
        quantity_increment = _decimal(item.get("quanto_multiplier"))
        minimum_contracts = _decimal(item.get("order_size_min"))
        leverage_min = _decimal(item.get("leverage_min"))
        leverage_max = _decimal(item.get("leverage_max"))
        if (
            contract.endswith("_USDT")
            and price_increment is not None
            and price_increment > 0
            and quantity_increment is not None
            and quantity_increment > 0
            and minimum_contracts is not None
            and minimum_contracts > 0
            and leverage_min is not None
            and leverage_max is not None
        ):
            contracts[contract] = {
                "price_increment": format(price_increment, "f"),
                "quantity_increment": format(quantity_increment, "f"),
                "minimum_quantity": format(minimum_contracts * quantity_increment, "f"),
                "contract_value": format(quantity_increment, "f"),
                "leverage_min": format(leverage_min, "f"),
                "leverage_max": format(leverage_max, "f"),
                "contract_enabled": not bool(item.get("in_delisting", False)),
                "funding_interval_seconds": item.get("funding_interval"),
                "funding_next_apply": item.get("funding_next_apply"),
            }
    if not contracts:
        raise RuntimeError("Gate contract price metadata is empty")
    return contracts


def _fetch_gate_json(url: str) -> object:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Autotrade-Paper/0.1"},
    )
    with urlopen(request, timeout=10) as response:
        if response.status != HTTPStatus.OK:
            raise RuntimeError(f"Gate returned HTTP {response.status}")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Gate tickers response exceeded the safety limit")
    return json.loads(body)


def request_is_local(headers: Message, port: int, *, require_origin: bool) -> bool:
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if headers.get("Host") not in allowed:
        return False
    return not require_origin or headers.get("Origin") in {f"http://{host}" for host in allowed}


class DashboardState:
    def __init__(
        self,
        report: RuntimeReport,
        settings: Settings,
        paper: PaperTrader,
        tradingview: TradingViewMonitor,
        *,
        config_path: Path | None = None,
        paper_factory: Callable[[], PaperTrader] | None = None,
        scope_controller: MarketScopeController | None = None,
    ) -> None:
        self._report = report
        self._settings = settings
        self._paper = paper
        self._tradingview = tradingview
        self._config_path = config_path
        self._paper_factory = paper_factory
        resolved_scope = scope_controller or getattr(paper, "scope_controller", None)
        if not isinstance(resolved_scope, MarketScopeController):
            resolved_scope = MarketScopeController(
                settings.log_directory / "market-scope.json",
                settings.log_directory / "market-scope-events.jsonl",
                settings.market_scope,
            )
        self._scope: MarketScopeController = resolved_scope
        self._lock = threading.Lock()
        self._markets: list[dict[str, object]] = []
        self._last_success_monotonic: float | None = None
        self._last_event_utc: str | None = None
        self._latency_ms: int | None = None
        self._feed_error: str | None = "Waiting for first Gate snapshot"
        self._execution_error: str | None = None
        self._manual_paused = False
        self._integrity_halted = True
        self._recovery: dict[str, object] | None = None
        self._entry_v3: dict[str, object] = {
            "status": "DISABLED",
            "strategy_status": "SHADOW",
            "execution_enabled": False,
            "candidate_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
        }

    @property
    def monitored_symbols(self) -> tuple[str, ...]:
        return self._paper.monitored_symbols

    def apply_snapshot(
        self,
        markets: list[dict[str, object]],
        latency_ms: int,
        *,
        monotonic_now: float | None = None,
    ) -> None:
        with self._lock:
            self._markets = markets
            self._last_success_monotonic = (
                monotonic_now if monotonic_now is not None else time.monotonic()
            )
            self._last_event_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            self._latency_ms = latency_ms
            self._feed_error = None
            try:
                self._paper.process(
                    markets,
                    entry_enabled=not self._manual_paused and not self._integrity_halted,
                )
                if self._paper.snapshot().risk_halted:
                    self._integrity_halted = True
            except Exception as exc:  # Strategy/execution boundary must fail closed.
                self._execution_error = str(exc)[:200]
                self._integrity_halted = True
                self._paper.set_entry_enabled(False)

    def apply_error(self, error: Exception) -> None:
        with self._lock:
            self._feed_error = str(error)[:200]
            self._paper.set_entry_enabled(False)

    def set_entry_v3_status(self, status: dict[str, object]) -> None:
        with self._lock:
            self._entry_v3 = dict(status)

    def _fresh(self, now: float) -> tuple[bool, float | None]:
        if self._last_success_monotonic is None:
            return False, None
        age = max(0.0, now - self._last_success_monotonic)
        return age <= self._settings.market_stale_after_seconds, age

    def pause(self) -> None:
        with self._lock:
            self._manual_paused = True
            self._paper.set_entry_enabled(False)

    def resume(self, *, monotonic_now: float | None = None) -> bool:
        with self._lock:
            now = monotonic_now if monotonic_now is not None else time.monotonic()
            return self._resume_locked(now)

    def _resume_locked(self, now: float) -> bool:
        fresh, _ = self._fresh(now)
        diagnostics = self._paper.snapshot().diagnostics
        storage = diagnostics.get("storage", {})
        if (
            not fresh
            or self._feed_error
            or self._execution_error
            or self._report.mode != "PAPER"
            or not self._report.risk_engine_enabled
            or not self._settings.strategy_enabled
            or not isinstance(storage, dict)
            or storage.get("safe") is not True
        ):
            return False
        if not self._paper.authorize_resume():
            return False
        self._manual_paused = False
        self._integrity_halted = False
        self._paper.set_entry_enabled(True)
        return True

    def analyze_and_repair(self, trigger: str) -> dict[str, object]:
        with self._lock:
            actions: list[str] = []
            checks: list[str] = []
            code, detail, next_action = self._diagnose_locked()
            if code not in {"STORAGE_LOW", "ENGINE_UNSAFE", "EXECUTION_FAULT"}:
                was_invalid = self._paper.snapshot().diagnostics.get("accounting", {})
                try:
                    valid = self._paper.recheck_integrity()
                except Exception as exc:  # A failed repair must also fail closed.
                    self._execution_error = f"Accounting recheck failed: {str(exc)[:160]}"
                    self._integrity_halted = True
                    self._paper.set_entry_enabled(False)
                    valid = False
                checks.append(
                    "Checkpoint and full fill ledger: " + ("VALID" if valid else "INVALID")
                )
                if valid and isinstance(was_invalid, dict) and was_invalid.get("state") != "VALID":
                    actions.append(
                        "Restored accounting from verified checkpoint and all split fills."
                    )
            code, detail, next_action = self._diagnose_locked()
            fresh, _ = self._fresh(time.monotonic())
            checks.append(
                "Public market data: " + ("LIVE" if fresh and not self._feed_error else "STALE")
            )
            checks.append(
                "PAPER mode and risk engine: "
                + (
                    "OK"
                    if self._report.mode == "PAPER" and self._report.risk_engine_enabled
                    else "FAILED"
                )
            )
            if code == "READY" and self._resume_locked(time.monotonic()):
                actions.append("Resumed PAPER entries after safety checks passed.")
                code, detail, next_action = self._diagnose_locked()
            self._recovery = {
                "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "status": "FIXED"
                if code == "HEALTHY" and actions
                else ("HEALTHY" if code == "HEALTHY" else "BLOCKED"),
                "code": code,
                "trigger": trigger,
                "detail": detail,
                "checks": checks,
                "actions": actions,
                "next_action": next_action,
            }
            return dict(self._recovery)

    def _diagnose_locked(self) -> tuple[str, str, str]:
        paper = self._paper.snapshot()
        diagnostics = paper.diagnostics
        accounting = diagnostics.get("accounting", {})
        storage = diagnostics.get("storage", {})
        risk = diagnostics.get("risk", {})
        if self._report.mode != "PAPER" or not self._report.risk_engine_enabled:
            return (
                "ENGINE_UNSAFE",
                "PAPER/risk engine safety check failed.",
                "Repair backend configuration.",
            )
        if not isinstance(storage, dict) or storage.get("safe") is not True:
            return (
                "STORAGE_LOW",
                "Insufficient safe storage for persistence.",
                "Free disk space, then retry.",
            )
        if self._execution_error:
            return (
                "EXECUTION_FAULT",
                self._execution_error,
                "Execution requires review; automatic resume is blocked.",
            )
        if not isinstance(accounting, dict) or accounting.get("state") != "VALID":
            reason = accounting.get("reason") if isinstance(accounting, dict) else None
            return (
                "ACCOUNTING_INVALID",
                str(reason or "Accounting could not be verified."),
                "Investigate or restore verified evidence; automatic reset is forbidden.",
            )
        if paper.strategy.get("status") == "RECOVERY_REQUIRED":
            return (
                "RECOVERY_REQUIRED",
                "A verified paper position survived a backend restart.",
                "Use FLATTEN PAPER POSITIONS against fresh data, then RESUME PAPER.",
            )
        if paper.risk_halted:
            reason = risk.get("state", "UNKNOWN") if isinstance(risk, dict) else "UNKNOWN"
            return (
                "RISK_HALT",
                f"Risk halt: {reason}.",
                "Review the risk event; limits remain enforced.",
            )
        fresh, _ = self._fresh(time.monotonic())
        if not fresh or self._feed_error:
            return (
                "FEED_UNHEALTHY",
                str(self._feed_error or "Market data is stale."),
                "Check the network; feed retries continue automatically.",
            )
        if self._manual_paused:
            return "MANUAL_PAUSE", "Entries were paused manually.", "Use RESUME PAPER when ready."
        if not self._settings.strategy_enabled:
            return (
                "STRATEGY_DISABLED",
                "Strategy is disabled in configuration.",
                "Enable the PAPER strategy in config and restart.",
            )
        if self._integrity_halted:
            return (
                "READY",
                "Startup checks passed; PAPER can resume.",
                "Retry analysis if resume fails.",
            )
        return (
            "HEALTHY",
            "Safety checks pass; PAPER entries are enabled.",
            "The strategy still waits for a qualified signal.",
        )

    def flatten(self) -> bool:
        with self._lock:
            return self._paper.flatten()

    def market_scope(self) -> dict[str, object]:
        with self._lock:
            paper = self._paper.snapshot()
            return self._scope.snapshot(paper.positions)

    def switch_market_scope(self, payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("market-scope payload must be an object")
        try:
            target = MarketScope(str(payload.get("scope", "")))
            policy = SwitchPolicy(str(payload.get("switch_policy", "")))
        except ValueError as exc:
            raise ValueError("invalid market scope or switch policy") from exc
        confirm_flatten = payload.get("confirm_flatten") is True
        with self._lock:
            paper = self._paper.snapshot()
            if target == MarketScope.XAU_ONLY:
                xau = next(
                    (
                        market
                        for market in self._markets
                        if market.get("symbol") == self._settings.xau.execution_symbol
                    ),
                    None,
                )
                if xau is None or xau.get("contract_enabled") is not True:
                    raise RuntimeError("Gate XAU_USDT contract metadata is unavailable or disabled")
            record = self._scope.request(
                target,
                policy,
                open_positions=paper.positions,
                confirm_flatten=confirm_flatten,
            )
            if record.switch_status == SwitchState.FLATTENING and not self._paper.flatten():
                latest = self._paper.snapshot()
                if latest.positions:
                    self._scope.fail("safe flatten could not be submitted")
                    raise RuntimeError("safe flatten could not be submitted")
            return self._scope.snapshot(self._paper.snapshot().positions)

    def start_new_paper_run(self) -> dict[str, object]:
        with self._lock:
            now = time.monotonic()
            fresh, _ = self._fresh(now)
            paper = self._paper.snapshot()
            diagnostics = paper.diagnostics
            accounting = diagnostics.get("accounting", {})
            risk = diagnostics.get("risk", {})
            storage = diagnostics.get("storage", {})
            config_path = self._config_path
            paper_factory = self._paper_factory
            allowed = (
                self._report.mode == "PAPER"
                and config_path is not None
                and paper_factory is not None
                and fresh
                and not self._feed_error
                and not self._execution_error
                and paper.positions == 0
                and paper.risk_halted
                and isinstance(accounting, dict)
                and accounting.get("state") == "VALID"
                and isinstance(risk, dict)
                and risk.get("state") == "MAX_DRAWDOWN"
                and isinstance(storage, dict)
                and storage.get("safe") is True
            )
            if not allowed:
                raise RuntimeError(
                    "new PAPER run requires a flat, reconciled MAX_DRAWDOWN halt with fresh data"
                )
            assert config_path is not None and paper_factory is not None

            prior_run = diagnostics.get("run", {})
            prior_run_id = (
                str(prior_run.get("run_id", "UNKNOWN"))
                if isinstance(prior_run, dict)
                else "UNKNOWN"
            )
            metadata = create_new_run(
                project_root=Path(__file__).resolve().parents[1],
                config_path=config_path,
                log_directory=self._settings.log_directory,
                starting_equity=self._settings.starting_balance_usdt,
            )
            self._paper.close()
            self._paper = paper_factory()
            self._manual_paused = False
            self._integrity_halted = True
            self._execution_error = None
            try:
                self._paper.process(self._markets, entry_enabled=False)
            except Exception as exc:
                self._execution_error = str(exc)[:200]
                self._paper.set_entry_enabled(False)
                raise RuntimeError(
                    f"new PAPER run created but initialization failed: {exc}"
                ) from exc
            if not self._resume_locked(now):
                raise RuntimeError("new PAPER run created but safety checks did not permit startup")
            self._recovery = {
                "checked_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "status": "FIXED",
                "code": "NEW_PAPER_RUN",
                "trigger": f"Archived MAX_DRAWDOWN run {prior_run_id}.",
                "detail": (
                    f"Started new PAPER run {metadata['run_id']} with configured risk limits."
                ),
                "checks": [
                    "Prior account: FLAT and ACCOUNTING VALID",
                    "Public market data: LIVE",
                    "Storage: SAFE",
                ],
                "actions": ["Archived prior run and created a new PAPER ledger."],
                "next_action": "The new strategy waits for a qualified signal.",
            }
            return metadata

    def close(self) -> None:
        self._tradingview.stop()
        with self._lock:
            self._paper.close()

    def snapshot(self, *, monotonic_now: float | None = None) -> dict[str, object]:
        with self._lock:
            now = monotonic_now if monotonic_now is not None else time.monotonic()
            fresh, age = self._fresh(now)
            feed_healthy = fresh and not self._feed_error
            if not feed_healthy:
                self._paper.set_entry_enabled(False)
            paper = self._paper.snapshot()
            diagnostics = paper.diagnostics
            accounting = diagnostics.get("accounting", {})
            accounting_state = (
                accounting.get("state", "VALID") if isinstance(accounting, dict) else "INVALID"
            )
            if paper.risk_halted or self._execution_error:
                self._integrity_halted = True
            trading_state = (
                "HALTED"
                if self._integrity_halted
                else "PAUSED"
                if self._manual_paused or not feed_healthy
                else "ACTIVE"
            )
            selected = sum(bool(market["selected"]) for market in self._markets)
            alerts = list(paper.alerts)
            if self._feed_error:
                alerts.insert(0, f"Gate feed: {self._feed_error}")
            elif not fresh:
                alerts.insert(
                    0,
                    "Gate feed is stale; new entries are paused until fresh data arrives.",
                )
            if self._execution_error:
                alerts.insert(0, f"Paper execution: {self._execution_error}")

            candidates = paper.strategy.get("candidates", [])
            candidate_map = (
                {
                    str(candidate["symbol"]): candidate
                    for candidate in candidates
                    if isinstance(candidate, dict) and "symbol" in candidate
                }
                if isinstance(candidates, list)
                else {}
            )
            symbol_diagnostics = diagnostics.get("symbols", {})
            raw_quarantined = (
                symbol_diagnostics.get("quarantined", {})
                if isinstance(symbol_diagnostics, dict)
                else {}
            )
            quarantined = raw_quarantined if isinstance(raw_quarantined, dict) else {}
            markets = [
                {
                    **market,
                    "signal_confidence": candidate_map.get(str(market.get("symbol")), {}).get(
                        "confidence", 0.0
                    ),
                    "signal_status": (
                        "QUARANTINED"
                        if str(market.get("symbol")) in quarantined
                        else candidate_map.get(str(market.get("symbol")), {}).get(
                            "status", "NOT_MONITORED"
                        )
                    ),
                    "quarantine_reason": quarantined.get(str(market.get("symbol"))),
                }
                for market in self._markets
            ]

            tradingview = self._tradingview.snapshot()
            research_symbol = self._settings.strategy_symbol
            research_market = next(
                (market for market in self._markets if market.get("symbol") == research_symbol),
                None,
            )
            tv_price = tradingview.get("price")
            gate_price = research_market.get("last") if research_market is not None else None
            if (
                isinstance(tv_price, (int, float))
                and isinstance(gate_price, (int, float))
                and gate_price > 0
            ):
                tradingview["gate_price"] = gate_price
                tradingview["price_divergence_pct"] = round(
                    (tv_price - gate_price) / gate_price * 100,
                    4,
                )
            tv_bias = tradingview.get("bias")
            primary_bias = paper.strategy.get("last_signal")
            if (
                paper.strategy.get("symbol") == research_symbol
                and tv_bias in {"LONG", "SHORT"}
                and primary_bias in {"LONG", "SHORT"}
            ):
                tradingview["nautilus_agreement"] = (
                    "AGREE" if tv_bias == primary_bias else "DISAGREE"
                )

            risk = diagnostics.get("risk", {})
            rollover_review = bool(
                isinstance(risk, dict)
                and risk.get("state") in {"DAILY_LOSS_REVIEW", "MAX_DRAWDOWN_REVIEW"}
                and risk.get("rollover_review_required") is True
            )
            resume_allowed = (
                fresh
                and not self._feed_error
                and not self._execution_error
                and (not paper.risk_halted or rollover_review)
                and accounting_state == "VALID"
                and trading_state != "ACTIVE"
            )

            storage = diagnostics.get("storage", {})
            if isinstance(storage, dict) and storage.get("safe") is False:
                alerts.insert(
                    0,
                    "Storage is below the safe persistence threshold; entries are blocked.",
                )
                resume_allowed = False

            new_paper_run_allowed = bool(
                self._config_path is not None
                and self._paper_factory is not None
                and fresh
                and not self._feed_error
                and not self._execution_error
                and paper.positions == 0
                and paper.risk_halted
                and accounting_state == "VALID"
                and isinstance(risk, dict)
                and risk.get("state") == "MAX_DRAWDOWN"
                and isinstance(storage, dict)
                and storage.get("safe") is True
            )

            return {
                "mode": self._report.mode,
                "venue": self._report.venue,
                "trading_state": trading_state,
                "engine": {
                    "status": "SIMULATION_READY",
                    "nautilus_version": self._report.nautilus_version,
                    "risk_engine_enabled": self._report.risk_engine_enabled,
                },
                "data": {
                    "status": "LIVE" if fresh and not self._feed_error else "STALE",
                    "source": "GATE_PUBLIC_REST",
                    "latency_ms": self._latency_ms,
                    "age_seconds": round(age, 1) if age is not None else None,
                    "last_event_utc": self._last_event_utc,
                    "error": self._feed_error,
                },
                "portfolio": paper.portfolio,
                "open_trade": paper.open_trade,
                "trade_history": paper.trade_history,
                "orders": paper.orders,
                "strategy": paper.strategy,
                "market_scope": self._scope.snapshot(paper.positions),
                "entry_v3": dict(self._entry_v3),
                "accounting": accounting,
                "risk": diagnostics.get("risk", {}),
                "profitability": diagnostics.get("profitability", {}),
                "recovery_timing": diagnostics.get("recovery_timing", {}),
                "experiments": diagnostics.get("experiments", {}),
                "execution_model": diagnostics.get("execution_model", {}),
                "run": diagnostics.get("run", {}),
                "storage": storage,
                "tradingview": tradingview,
                "markets": markets,
                "active_symbols": selected,
                "alerts": alerts,
                "recovery": self._recovery,
                "controls": {
                    "pause_allowed": trading_state == "ACTIVE",
                    "resume_allowed": resume_allowed,
                    "auto_resume_allowed": (
                        resume_allowed and not self._manual_paused and not rollover_review
                    ),
                    "flatten_allowed": paper.positions > 0,
                    "new_paper_run_allowed": new_paper_run_allowed,
                },
            }


class GatePoller:
    def __init__(self, state: DashboardState, settings: Settings, logger: logging.Logger) -> None:
        self._state = state
        self._settings = settings
        self._logger = logger
        self._price_increments: dict[str, str] | None = None
        self._contract_metadata: dict[str, dict[str, object]] | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._poll_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="gate-public-feed", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def refresh(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._wake.wait(self._settings.market_poll_seconds)
            self._wake.clear()

    def poll_once(self) -> None:
        with self._poll_lock:
            started = time.monotonic()
            try:
                if self._contract_metadata is None:
                    self._contract_metadata = fetch_gate_contracts()
                    self._price_increments = {
                        symbol: str(metadata["price_increment"])
                        for symbol, metadata in self._contract_metadata.items()
                    }
                markets = rank_tickers(
                    fetch_gate_tickers(),
                    self._settings,
                    self._state.monitored_symbols,
                    self._price_increments,
                    self._contract_metadata,
                )
                if not any(bool(market["selected"]) for market in markets):
                    raise RuntimeError("no Gate contracts passed the configured filters")
                latency_ms = round((time.monotonic() - started) * 1000)
                self._state.apply_snapshot(markets, latency_ms)
            except Exception as exc:  # Network/parser boundary must fail closed.
                self._state.apply_error(exc)
                self._logger.warning("Gate public feed failed: %s", exc)


class EntryV3ShadowRunner:
    def __init__(self, state: DashboardState, settings: Settings, logger: logging.Logger) -> None:
        self._state = state
        self._settings = settings
        self._logger = logger
        self._shadow: LiveEntryV3Shadow | None = None
        self._thread: threading.Thread | None = None

    def start(self, symbols: tuple[str, ...]) -> None:
        if not self._settings.entry_v3.enabled or not self._settings.entry_v3.shadow_enabled:
            return
        if not symbols:
            self._state.set_entry_v3_status({"status": "BLOCKED", "error": "No monitored symbols."})
            return
        self._shadow = LiveEntryV3Shadow(
            self._settings,
            symbols,
            self._settings.log_directory / "entry-v3-captures",
            self._state.set_entry_v3_status,
        )
        self._state.set_entry_v3_status(self._shadow.status())
        self._thread = threading.Thread(target=self._run, name="entry-v3-shadow", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        assert self._shadow is not None
        try:
            self._shadow.run()
        except Exception as exc:
            self._logger.warning("Entry V3 shadow stopped: %s", exc)
        finally:
            self._state.set_entry_v3_status(self._shadow.status())


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        state: DashboardState,
        poller: GatePoller,
        logger: logging.Logger,
    ) -> None:
        super().__init__(address, DashboardHandler)
        self.state = state
        self.poller = poller
        self.repair_lock = threading.Lock()
        self.logger = logger


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"
    server_version = "Autotrade"
    sys_version = ""

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self._send(status, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:  # noqa: N802
        if not request_is_local(self.headers, self.server.server_port, require_origin=False):
            self._json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "invalid host"})
            return
        if self.path == "/api/state":
            self._json(HTTPStatus.OK, self.server.state.snapshot())
            return
        if self.path == "/api/market-scope":
            self._json(HTTPStatus.OK, self.server.state.market_scope())
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        }
        asset = assets.get(self.path)
        if asset is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            body = (STATIC_DIR / asset[0]).read_bytes()
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "dashboard asset unavailable"})
            return
        self._send(HTTPStatus.OK, asset[1], body)

    def do_POST(self) -> None:  # noqa: N802
        if not request_is_local(self.headers, self.server.server_port, require_origin=True):
            self._json(HTTPStatus.FORBIDDEN, {"error": "same-origin request required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return
        if length < 0 or length > 1024:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
            return
        body = self.rfile.read(length) if length else b""

        if self.path == "/api/market-scope":
            try:
                payload = json.loads(body or b"{}")
                result = self.server.state.switch_market_scope(payload)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:200]})
                return
            except (RuntimeError, OSError) as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)[:200]})
                return
            self.server.logger.info("Market scope state: %s", json.dumps(result))
            self._json(HTTPStatus.OK, result)
            return
        if self.path == "/api/control/pause":
            self.server.state.pause()
        elif self.path == "/api/control/resume":
            if not self.server.state.resume():
                self._json(
                    HTTPStatus.CONFLICT,
                    {"error": "fresh data and valid accounting/risk state are required"},
                )
                return
        elif self.path == "/api/control/restart-feed":
            self.server.poller.refresh()
        elif self.path == "/api/control/analyze-repair":
            if not self.server.repair_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "Analysis is already running."})
                return
            try:
                before = self.server.state.snapshot()
                trigger = f"{before['trading_state']}: {before['alerts']}"
                self.server.poller.poll_once()
                result = self.server.state.analyze_and_repair(trigger)
                self.server.logger.info("Analyze & auto-fix: %s", json.dumps(result))
            finally:
                self.server.repair_lock.release()
        elif self.path == "/api/control/flatten":
            try:
                flattened = self.server.state.flatten()
            except (RuntimeError, ValueError, OSError) as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)[:200]})
                return
            if not flattened:
                self._json(HTTPStatus.CONFLICT, {"error": "no paper position to flatten"})
                return
        elif self.path == "/api/control/new-paper-run":
            try:
                payload = json.loads(body or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            if not isinstance(payload, dict) or payload.get("confirm_new_run") is not True:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "explicit confirmation required"})
                return
            try:
                metadata = self.server.state.start_new_paper_run()
            except (RuntimeError, ValueError, OSError) as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)[:200]})
                return
            self.server.logger.info("Started new PAPER run: %s", metadata.get("run_id"))
        elif self.path == "/api/control/shutdown":
            self.server.state.pause()
            self._json(HTTPStatus.OK, self.server.state.snapshot())
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._json(HTTPStatus.OK, self.server.state.snapshot())

    def log_message(self, message: str, *args: object) -> None:
        self.server.logger.info("%s %s", self.client_address[0], message % args)


def _logger(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("autotrade.dashboard")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(
        settings.log_directory / "dashboard.log",
        maxBytes=settings.log_file_max_size,
        backupCount=settings.log_file_max_backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def run_dashboard(settings: Settings, config_path: Path | None = None) -> None:
    report = run_paper_smoke(settings)
    logger = _logger(settings)
    scope = MarketScopeController(
        settings.log_directory / "market-scope.json",
        settings.log_directory / "market-scope-events.jsonl",
        settings.market_scope,
    )
    paper = PaperTrader(report, settings, logger, scope)
    tradingview = build_tradingview_monitor(settings, logger)
    resolved_config = (config_path or Path("config/paper.toml")).resolve()
    state = DashboardState(
        report,
        settings,
        paper,
        tradingview,
        config_path=resolved_config,
        paper_factory=lambda: PaperTrader(report, settings, logger, scope),
        scope_controller=scope,
    )
    poller = GatePoller(state, settings, logger)
    server = DashboardServer(
        (settings.dashboard_host, settings.dashboard_port), state, poller, logger
    )
    tradingview.start()
    poller.poll_once()
    shadow = EntryV3ShadowRunner(state, settings, logger)
    shadow.start(state.monitored_symbols)
    poller.start()
    logger.info("Dashboard started on http://%s:%s", *server.server_address)
    print(
        f"Dashboard running at http://{settings.dashboard_host}:{settings.dashboard_port}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        shadow.stop()
        poller.stop()
        state.close()
        server.server_close()
        logger.info("Dashboard stopped")
