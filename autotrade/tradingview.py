from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from autotrade.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRADINGVIEW_CLI = PROJECT_ROOT / "vendor" / "tradingview-mcp" / "src" / "cli" / "index.js"
BUNDLED_NODE = (
    Path.home()
    / ".cache"
    / "codex-runtimes"
    / "codex-primary-runtime"
    / "dependencies"
    / "node"
    / "bin"
    / "node.exe"
)
MAX_CLI_OUTPUT_BYTES = 65_536
MAX_INDICATOR_VALUES = 64
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,24}_USDT$")
TV_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:!/=\-]{1,96}$")


class TradingViewStatus(StrEnum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


class TradingViewError(RuntimeError):
    def __init__(self, message: str, status: TradingViewStatus) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class TradingSymbolMap:
    gate_symbol: str
    nautilus_symbol: str
    tradingview_symbol: str
    base_asset: str
    quote_asset: str
    market_type: str


@dataclass(frozen=True)
class TradingViewHealth:
    status: TradingViewStatus
    chart_symbol: str | None
    timeframe: str | None
    latency_ms: int | None
    observed_monotonic: float
    observed_utc: str | None
    error: str | None
    price: float | None = None
    open_price: float | None = None
    indicator_values: tuple[tuple[str, str, float], ...] = ()


def map_gate_symbol(gate_symbol: str) -> TradingSymbolMap:
    normalized = gate_symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(normalized):
        raise ValueError("Gate symbol must be an ASCII BASE_USDT perpetual contract")
    base = normalized.removesuffix("_USDT")
    return TradingSymbolMap(
        gate_symbol=normalized,
        nautilus_symbol=f"{normalized}-PERP.GATE",
        tradingview_symbol=f"GATE:{base}USDT.P",
        base_asset=base,
        quote_asset="USDT",
        market_type="PERPETUAL",
    )


def _node_command() -> str | None:
    configured = os.getenv("TRADINGVIEW_NODE_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        return str(candidate.resolve()) if candidate.is_file() else None
    if BUNDLED_NODE.is_file():
        return str(BUNDLED_NODE)
    return shutil.which("node")


def _safe_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not TV_VALUE_RE.fullmatch(value):
        raise TradingViewError(
            f"TradingView returned an invalid {field}", TradingViewStatus.UNAVAILABLE
        )
    return value


class TradingViewAdapter:
    """Strict, read-only wrapper around pinned TradingView research commands."""

    def __init__(
        self,
        *,
        timeout_ms: int,
        cache_enabled: bool,
        cache_ttl_seconds: int,
        failure_threshold: int = 3,
        circuit_reset_seconds: int = 60,
        retries: int = 1,
        node_command: str | None = None,
        cli_path: Path = TRADINGVIEW_CLI,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout_seconds = timeout_ms / 1000
        self._cache_enabled = cache_enabled
        self._cache_ttl_seconds = cache_ttl_seconds
        self._failure_threshold = failure_threshold
        self._circuit_reset_seconds = circuit_reset_seconds
        self._retries = retries
        self._node = node_command if node_command is not None else _node_command()
        self._cli_path = cli_path.resolve()
        self._runner = runner
        self._clock = clock
        self._failures = 0
        self._circuit_open_until = 0.0
        self._cached: TradingViewHealth | None = None

    def get_health(self) -> TradingViewHealth:
        now = self._clock()
        if now < self._circuit_open_until:
            return self._failure(
                TradingViewStatus.UNAVAILABLE,
                "TradingView circuit breaker is open",
                now,
            )
        if (
            self._cache_enabled
            and self._cached is not None
            and now - self._cached.observed_monotonic < self._cache_ttl_seconds
        ):
            return self._cached

        failure: TradingViewError | None = None
        for _ in range(self._retries + 1):
            try:
                result = self._execute_research()
            except TradingViewError as exc:
                failure = exc
                continue
            self._failures = 0
            self._circuit_open_until = 0.0
            if self._cache_enabled:
                self._cached = result
            return result

        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._circuit_open_until = self._clock() + self._circuit_reset_seconds
        assert failure is not None
        return self._failure(failure.status, str(failure), self._clock())

    def _execute(self, *arguments: str) -> tuple[object, int]:
        if self._node is None or not self._cli_path.is_file():
            raise TradingViewError(
                "TradingView CLI runtime is not installed", TradingViewStatus.UNAVAILABLE
            )
        environment = os.environ.copy()
        environment["TV_CDP_HOST"] = "127.0.0.1"
        environment["TV_CDP_PORT"] = "9222"
        started = self._clock()
        try:
            completed = self._runner(
                [self._node, str(self._cli_path), *arguments],
                cwd=self._cli_path.parents[2],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise TradingViewError(
                "TradingView CLI timed out", TradingViewStatus.UNAVAILABLE
            ) from exc
        except OSError as exc:
            raise TradingViewError(
                "TradingView CLI could not start", TradingViewStatus.UNAVAILABLE
            ) from exc
        latency_ms = round((self._clock() - started) * 1000)
        if completed.returncode != 0:
            status = (
                TradingViewStatus.DISCONNECTED
                if completed.returncode == 2
                else TradingViewStatus.UNAVAILABLE
            )
            raise TradingViewError("TradingView Desktop/CDP is unavailable", status)
        if len(completed.stdout.encode("utf-8")) > MAX_CLI_OUTPUT_BYTES:
            raise TradingViewError(
                "TradingView CLI output exceeded the safety limit",
                TradingViewStatus.UNAVAILABLE,
            )
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise TradingViewError(
                "TradingView CLI returned malformed JSON", TradingViewStatus.UNAVAILABLE
            ) from exc
        return payload, latency_ms

    def _execute_research(self) -> TradingViewHealth:
        payload, latency_ms = self._execute("status")
        health = self._parse_health(payload, latency_ms)
        if health.status != TradingViewStatus.CONNECTED:
            return health

        errors = []
        price = None
        open_price = None
        indicators: tuple[tuple[str, str, float], ...] = ()
        total_latency = latency_ms
        try:
            quote, quote_latency = self._execute("quote")
            price, open_price = self._parse_quote(quote)
            total_latency += quote_latency
        except TradingViewError as exc:
            errors.append(str(exc))
        try:
            values, values_latency = self._execute("values")
            indicators = self._parse_indicator_values(values)
            total_latency += values_latency
        except TradingViewError as exc:
            errors.append(str(exc))
        return replace(
            health,
            status=TradingViewStatus.DEGRADED if errors else health.status,
            latency_ms=total_latency,
            error="; ".join(errors) or health.error,
            price=price,
            open_price=open_price,
            indicator_values=indicators,
        )

    @staticmethod
    def _parse_quote(payload: object) -> tuple[float, float | None]:
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise TradingViewError(
                "TradingView quote schema changed", TradingViewStatus.UNAVAILABLE
            )
        raw_price = payload.get("last", payload.get("close"))
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            raise TradingViewError(
                "TradingView quote is invalid", TradingViewStatus.UNAVAILABLE
            )
        price = float(raw_price)
        if not math.isfinite(price) or price <= 0:
            raise TradingViewError(
                "TradingView quote is invalid", TradingViewStatus.UNAVAILABLE
            )
        raw_open = payload.get("open")
        open_price = (
            float(raw_open)
            if not isinstance(raw_open, bool)
            and isinstance(raw_open, (int, float))
            and math.isfinite(float(raw_open))
            and float(raw_open) > 0
            else None
        )
        return price, open_price

    @staticmethod
    def _parse_indicator_values(payload: object) -> tuple[tuple[str, str, float], ...]:
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or not isinstance(payload.get("studies"), list)
        ):
            raise TradingViewError(
                "TradingView indicator schema changed", TradingViewStatus.UNAVAILABLE
            )
        parsed = []
        for study in payload["studies"][:32]:
            if not isinstance(study, dict) or not isinstance(study.get("values"), dict):
                continue
            name = str(study.get("name", "")).strip()
            if not name or len(name) > 64 or not name.isprintable():
                continue
            for label, raw_value in list(study["values"].items())[:16]:
                label = str(label).strip()
                if (
                    not label
                    or len(label) > 64
                    or not label.isprintable()
                    or isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                ):
                    continue
                value = float(raw_value)
                if math.isfinite(value):
                    parsed.append((name, label, value))
                if len(parsed) == MAX_INDICATOR_VALUES:
                    return tuple(parsed)
        return tuple(parsed)

    def _parse_health(self, payload: object, latency_ms: int) -> TradingViewHealth:
        if not isinstance(payload, dict):
            raise TradingViewError(
                "TradingView CLI schema changed", TradingViewStatus.UNAVAILABLE
            )
        if not isinstance(payload.get("success"), bool) or not isinstance(
            payload.get("cdp_connected"), bool
        ):
            raise TradingViewError(
                "TradingView CLI schema changed", TradingViewStatus.UNAVAILABLE
            )
        if payload["success"] is not True or payload["cdp_connected"] is not True:
            raise TradingViewError(
                "TradingView CLI reported a disconnected session",
                TradingViewStatus.DISCONNECTED,
            )
        if not isinstance(payload.get("api_available"), bool):
            raise TradingViewError(
                "TradingView CLI schema changed", TradingViewStatus.UNAVAILABLE
            )
        chart_symbol = _safe_value(payload.get("chart_symbol"), "chart symbol")
        timeframe = _safe_value(payload.get("chart_resolution"), "timeframe")
        observed = self._clock()
        return TradingViewHealth(
            status=(
                TradingViewStatus.CONNECTED
                if payload["api_available"]
                else TradingViewStatus.DEGRADED
            ),
            chart_symbol=chart_symbol,
            timeframe=timeframe,
            latency_ms=latency_ms,
            observed_monotonic=observed,
            observed_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            error=None if payload["api_available"] else "TradingView chart API is unavailable",
        )

    @staticmethod
    def _failure(
        status: TradingViewStatus, message: str, now: float
    ) -> TradingViewHealth:
        return TradingViewHealth(status, None, None, None, now, None, message)


class TradingViewMonitor:
    """Background health observer; it has no reference to strategy, risk, or execution objects."""

    def __init__(
        self,
        settings: Settings,
        adapter: TradingViewAdapter,
        logger: logging.Logger,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._adapter = adapter
        self._logger = logger
        self._clock = clock
        self._mapping = map_gate_symbol(settings.strategy_symbol)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reading = TradingViewHealth(
            TradingViewStatus.DISABLED
            if not settings.tradingview_enabled
            else TradingViewStatus.UNAVAILABLE,
            None,
            None,
            None,
            self._clock(),
            None,
            None if not settings.tradingview_enabled else "Waiting for TradingView health check",
        )

    def start(self) -> None:
        if not self._settings.tradingview_enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="tradingview-health", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def refresh(self) -> None:
        if not self._settings.tradingview_enabled:
            return
        reading = self._adapter.get_health()
        with self._lock:
            previous = self._reading.status
            self._reading = reading
        if reading.status != previous and reading.status not in {
            TradingViewStatus.CONNECTED,
            TradingViewStatus.DISABLED,
        }:
            self._logger.warning("TradingView health is %s", reading.status)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            reading = self._reading
        now = self._clock()
        status = reading.status
        error = reading.error
        freshness_ms = (
            round(max(0.0, now - reading.observed_monotonic) * 1000)
            if reading.observed_utc is not None
            else None
        )
        if (
            status in {TradingViewStatus.CONNECTED, TradingViewStatus.DEGRADED}
            and freshness_ms is not None
            and freshness_ms > self._settings.tradingview_stale_after_seconds * 1000
        ):
            status = TradingViewStatus.STALE
            error = "TradingView health snapshot is stale"

        expected_symbol = self._mapping.tradingview_symbol
        symbol_matches = (
            reading.chart_symbol is not None
            and reading.chart_symbol.removeprefix("=").upper() == expected_symbol
        )
        timeframe_matches = reading.timeframe == self._settings.tradingview_expected_timeframe
        if status == TradingViewStatus.CONNECTED and not symbol_matches:
            status = TradingViewStatus.DEGRADED
            error = "TradingView chart symbol differs from the paper strategy"
        elif status == TradingViewStatus.CONNECTED and not timeframe_matches:
            status = TradingViewStatus.DEGRADED
            error = "TradingView chart timeframe differs from configuration"

        return {
            "enabled": self._settings.tradingview_enabled,
            "status": status.value,
            "advisory_only": True,
            "execution_influence": "NONE",
            "symbol": reading.chart_symbol,
            "expected_symbol": expected_symbol,
            "timeframe": reading.timeframe,
            "expected_timeframe": self._settings.tradingview_expected_timeframe,
            "price": reading.price,
            "bias": (
                "LONG"
                if reading.price is not None
                and reading.open_price is not None
                and reading.price > reading.open_price
                else "SHORT"
                if reading.price is not None
                and reading.open_price is not None
                and reading.price < reading.open_price
                else "NEUTRAL"
                if reading.price is not None and reading.open_price is not None
                else "UNAVAILABLE"
            ),
            "confidence": None,
            "regime": "UNAVAILABLE",
            "latency_ms": reading.latency_ms,
            "freshness_ms": freshness_ms,
            "pine_signal": "UNAVAILABLE",
            "nautilus_agreement": "UNAVAILABLE",
            "indicator_count": len({item[0] for item in reading.indicator_values}),
            "indicators": [
                {"study": study, "name": name, "value": value}
                for study, name, value in reading.indicator_values
            ],
            "confirmation_mode": self._settings.tradingview_confirmation_mode,
            "configured_weight": float(self._settings.tradingview_weight),
            "source_timestamp": reading.observed_utc,
            "pine_enabled": self._settings.tradingview_pine_enabled,
            "screenshot_enabled": self._settings.tradingview_screenshot_enabled,
            "error": error,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self._settings.tradingview_poll_seconds)


def build_tradingview_monitor(
    settings: Settings, logger: logging.Logger
) -> TradingViewMonitor:
    adapter = TradingViewAdapter(
        timeout_ms=settings.tradingview_timeout_ms,
        cache_enabled=settings.tradingview_cache_enabled,
        cache_ttl_seconds=settings.tradingview_cache_ttl_seconds,
    )
    return TradingViewMonitor(settings, adapter, logger)
