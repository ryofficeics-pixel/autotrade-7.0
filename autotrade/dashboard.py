from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.request import Request, urlopen

from autotrade.config import Settings
from autotrade.paper import PaperTrader
from autotrade.runtime import RuntimeReport, run_paper_smoke
from autotrade.tradingview import TradingViewMonitor, build_tradingview_monitor

GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
MAX_RESPONSE_BYTES = 2_000_000
STATIC_DIR = Path(__file__).resolve().parents[1] / "dashboard"


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
) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ValueError("Gate tickers response is not a list")

    eligible: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        contract = str(item.get("contract", ""))
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
            "screen_score": round(score, 1),
            "selected": rejection is None,
            "rejection": rejection,
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
    selected = [market for market in eligible if market["symbol"] in required]
    selected.extend(
        market
        for market in eligible
        if market["symbol"] not in required
    )
    selected = selected[: settings.active_symbols]
    return selected + rejected[: max(0, 12 - len(selected))]


def fetch_gate_tickers() -> object:
    request = Request(
        GATE_TICKERS_URL,
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
    ) -> None:
        self._report = report
        self._settings = settings
        self._paper = paper
        self._tradingview = tradingview
        self._lock = threading.Lock()
        self._markets: list[dict[str, object]] = []
        self._last_success_monotonic: float | None = None
        self._last_event_utc: str | None = None
        self._latency_ms: int | None = None
        self._feed_error: str | None = "Waiting for first Gate snapshot"
        self._execution_error: str | None = None
        self._manual_paused = False
        self._integrity_halted = True

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
                self._execution_error = None
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
            fresh, _ = self._fresh(now)
            if not fresh or self._feed_error or self._execution_error:
                return False
            self._manual_paused = False
            self._integrity_halted = False
            self._paper.set_entry_enabled(True)
            return True

    def flatten(self) -> bool:
        with self._lock:
            return self._paper.flatten()

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
            candidate_map = {
                str(candidate["symbol"]): candidate
                for candidate in candidates
                if isinstance(candidate, dict) and "symbol" in candidate
            } if isinstance(candidates, list) else {}
            markets = [
                {
                    **market,
                    "signal_confidence": candidate_map.get(
                        str(market.get("symbol")), {}
                    ).get("confidence", 0.0),
                    "signal_status": candidate_map.get(
                        str(market.get("symbol")), {}
                    ).get("status", "NOT_MONITORED"),
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

            resume_allowed = (
                fresh
                and not self._feed_error
                and not self._execution_error
                and not paper.risk_halted
                and trading_state != "ACTIVE"
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
                "tradingview": tradingview,
                "markets": markets,
                "active_symbols": selected,
                "alerts": alerts,
                "controls": {
                    "pause_allowed": trading_state == "ACTIVE",
                    "resume_allowed": resume_allowed,
                    "auto_resume_allowed": resume_allowed and not self._manual_paused,
                    "flatten_allowed": paper.positions > 0,
                },
            }


class GatePoller:
    def __init__(self, state: DashboardState, settings: Settings, logger: logging.Logger) -> None:
        self._state = state
        self._settings = settings
        self._logger = logger
        self._stop = threading.Event()
        self._wake = threading.Event()
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
            started = time.monotonic()
            try:
                markets = rank_tickers(
                    fetch_gate_tickers(),
                    self._settings,
                    self._state.monitored_symbols,
                )
                if not any(bool(market["selected"]) for market in markets):
                    raise RuntimeError("no Gate contracts passed the configured filters")
                latency_ms = round((time.monotonic() - started) * 1000)
                self._state.apply_snapshot(markets, latency_ms)
            except Exception as exc:  # Network/parser boundary must fail closed.
                self._state.apply_error(exc)
                self._logger.warning("Gate public feed failed: %s", exc)
            self._wake.wait(self._settings.market_poll_seconds)
            self._wake.clear()


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
        if length:
            self.rfile.read(length)

        if self.path == "/api/control/pause":
            self.server.state.pause()
        elif self.path == "/api/control/resume":
            if not self.server.state.resume():
                self._json(HTTPStatus.CONFLICT, {"error": "fresh Gate state is required"})
                return
        elif self.path == "/api/control/restart-feed":
            self.server.poller.refresh()
        elif self.path == "/api/control/flatten":
            if not self.server.state.flatten():
                self._json(HTTPStatus.CONFLICT, {"error": "no paper position to flatten"})
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


def run_dashboard(settings: Settings) -> None:
    report = run_paper_smoke(settings)
    logger = _logger(settings)
    paper = PaperTrader(report, settings, logger)
    tradingview = build_tradingview_monitor(settings, logger)
    state = DashboardState(report, settings, paper, tradingview)
    poller = GatePoller(state, settings, logger)
    server = DashboardServer(
        (settings.dashboard_host, settings.dashboard_port), state, poller, logger
    )
    tradingview.start()
    poller.start()
    logger.info("Dashboard started on http://%s:%s", *server.server_address)
    print(
        f"Dashboard running at http://{settings.dashboard_host}:{settings.dashboard_port}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        poller.stop()
        state.close()
        server.server_close()
        logger.info("Dashboard stopped")
