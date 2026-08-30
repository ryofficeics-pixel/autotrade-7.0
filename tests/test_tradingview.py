from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autotrade.config import Settings, load_settings
from autotrade.tradingview import (
    TRADINGVIEW_CLI,
    TradingViewAdapter,
    TradingViewMonitor,
    map_gate_symbol,
)

SAFE_ENV = {
    "TRADING_MODE": "PAPER",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION": "",
}


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def connected_payload(
    symbol: str = "GATE:ETHUSDT.P", timeframe: str = "5"
) -> dict[str, object]:
    return {
        "success": True,
        "cdp_connected": True,
        "chart_symbol": symbol,
        "chart_resolution": timeframe,
        "api_available": True,
    }


def research_payload(
    command: list[str],
    symbol: str = "GATE:ETHUSDT.P",
    timeframe: str = "5",
) -> dict[str, object]:
    if command[-1] == "status":
        return connected_payload(symbol, timeframe)
    if command[-1] == "quote":
        return {"success": True, "symbol": symbol, "open": 2490.0, "last": 2501.25}
    if command[-1] == "values":
        return {
            "success": True,
            "study_count": 1,
            "studies": [{"name": "Relative Strength Index", "values": {"RSI": 57.4}}],
        }
    raise AssertionError(f"unexpected command: {command}")


class TradingViewTests(unittest.TestCase):
    def settings(
        self,
        *,
        tradingview_enabled: bool = False,
        tradingview_poll_seconds: int = 15,
        tradingview_stale_after_seconds: int = 45,
    ) -> Settings:
        with patch.dict(os.environ, SAFE_ENV, clear=True):
            return replace(
                load_settings(),
                tradingview_enabled=tradingview_enabled,
                tradingview_poll_seconds=tradingview_poll_seconds,
                tradingview_stale_after_seconds=tradingview_stale_after_seconds,
            )

    def adapter(
        self,
        runner: object,
        clock: Clock | None = None,
        *,
        retries: int = 1,
        failure_threshold: int = 3,
        circuit_reset_seconds: int = 60,
    ) -> TradingViewAdapter:
        return TradingViewAdapter(
            timeout_ms=2500,
            cache_enabled=False,
            cache_ttl_seconds=15,
            node_command=sys.executable,
            cli_path=TRADINGVIEW_CLI,
            runner=runner,  # type: ignore[arg-type]
            clock=clock or Clock(),
            retries=retries,
            failure_threshold=failure_threshold,
            circuit_reset_seconds=circuit_reset_seconds,
        )

    def test_symbol_mapping_is_centralized_and_fail_closed(self) -> None:
        mapping = map_gate_symbol("btc_usdt")
        self.assertEqual(mapping.nautilus_symbol, "BTC_USDT-PERP.GATE")
        self.assertEqual(mapping.tradingview_symbol, "GATE:BTCUSDT.P")
        with self.assertRaisesRegex(ValueError, "BASE_USDT"):
            map_gate_symbol("BTC_USDT;calc.exe")

    def test_adapter_uses_argument_list_and_forces_local_cdp(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command, 0, json.dumps(research_payload(command)), ""
            )

        result = self.adapter(runner).get_health()

        self.assertEqual(result.status, "CONNECTED")
        self.assertEqual([call[0][-1] for call in calls], ["status", "quote", "values"])
        self.assertEqual(result.price, 2501.25)
        self.assertEqual(result.indicator_values, (("Relative Strength Index", "RSI", 57.4),))
        for _, options in calls:
            self.assertIs(options["shell"], False)
            environment = options["env"]
            self.assertIsInstance(environment, dict)
            self.assertEqual(environment["TV_CDP_HOST"], "127.0.0.1")  # type: ignore[index]
            self.assertEqual(environment["TV_CDP_PORT"], "9222")  # type: ignore[index]

    def test_timeout_retries_without_raising(self) -> None:
        calls = 0

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])  # type: ignore[arg-type]

        result = self.adapter(runner).get_health()

        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertEqual(calls, 2)

    def test_disconnected_cli_and_invalid_json_are_isolated(self) -> None:
        def disconnected(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 2, "", '{"secret":"not logged"}')

        def malformed(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, "not-json", "")

        self.assertEqual(self.adapter(disconnected).get_health().status, "DISCONNECTED")
        invalid = self.adapter(malformed).get_health()
        self.assertEqual(invalid.status, "UNAVAILABLE")
        self.assertNotIn("not-json", invalid.error or "")

    def test_schema_break_is_unavailable(self) -> None:
        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, '{"success":true}', "")

        result = self.adapter(runner).get_health()
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertIn("schema", result.error or "")

    def test_cache_avoids_duplicate_cli_calls(self) -> None:
        clock = Clock()
        calls = 0

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(
                command, 0, json.dumps(research_payload(command)), ""
            )

        adapter = TradingViewAdapter(
            timeout_ms=2500,
            cache_enabled=True,
            cache_ttl_seconds=15,
            node_command=sys.executable,
            cli_path=TRADINGVIEW_CLI,
            runner=runner,
            clock=clock,
        )
        adapter.get_health()
        clock.now += 14
        adapter.get_health()
        self.assertEqual(calls, 3)

    def test_circuit_breaker_skips_calls_until_reset(self) -> None:
        clock = Clock()
        calls = 0

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(command, 2, "", "")

        adapter = self.adapter(
            runner,
            clock,
            retries=0,
            failure_threshold=2,
            circuit_reset_seconds=60,
        )
        adapter.get_health()
        adapter.get_health()
        open_result = adapter.get_health()
        self.assertEqual(open_result.status, "UNAVAILABLE")
        self.assertEqual(calls, 2)
        clock.now += 61
        adapter.get_health()
        self.assertEqual(calls, 3)

    def test_monitor_marks_wrong_chart_and_stale_health_degraded(self) -> None:
        clock = Clock()

        def wrong_chart(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(research_payload(command, "NASDAQ:VERYLONGSYMBOLNAME", "15")),
                "",
            )

        settings = self.settings(
            tradingview_enabled=True,
            tradingview_poll_seconds=10,
            tradingview_stale_after_seconds=20,
        )
        monitor = TradingViewMonitor(
            settings, self.adapter(wrong_chart, clock), logging.getLogger("test.tv"), clock=clock
        )
        monitor.refresh()
        mismatch = monitor.snapshot()
        self.assertEqual(mismatch["status"], "DEGRADED")
        self.assertEqual(mismatch["execution_influence"], "NONE")
        self.assertEqual(mismatch["price"], 2501.25)
        self.assertEqual(mismatch["bias"], "LONG")
        self.assertEqual(mismatch["indicator_count"], 1)

        clock.now += 21
        stale = monitor.snapshot()
        self.assertEqual(stale["status"], "STALE")

    def test_missing_cli_is_unavailable(self) -> None:
        adapter = TradingViewAdapter(
            timeout_ms=2500,
            cache_enabled=False,
            cache_ttl_seconds=15,
            node_command=str(Path(sys.executable)),
            cli_path=Path(__file__).with_name("missing.js"),
        )
        self.assertEqual(adapter.get_health().status, "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
