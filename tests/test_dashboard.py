from __future__ import annotations

import json
import logging
import os
import threading
import unittest
from dataclasses import replace
from decimal import Decimal
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from autotrade.config import Settings, load_settings
from autotrade.dashboard import (
    DashboardServer,
    DashboardState,
    fetch_gate_price_increments,
    rank_tickers,
    request_is_local,
)
from autotrade.paper import PaperSnapshot
from autotrade.runtime import RuntimeReport

SAFE_ENV = {
    "TRADING_MODE": "PAPER",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION": "",
}


class DashboardTests(unittest.TestCase):
    def settings(self) -> Settings:
        with patch.dict(os.environ, SAFE_ENV, clear=True):
            return load_settings()

    @staticmethod
    def paper() -> object:
        class FakePaper:
            risk_halted = False
            accounting_state = "VALID"
            risk_state = "OK"
            strategy_status = "PAUSED"
            storage_safe = True
            rechecks = 0
            closed = False

            def process(self, markets: object, *, entry_enabled: bool) -> None:
                self.entry_enabled = entry_enabled

            def set_entry_enabled(self, enabled: bool) -> None:
                self.entry_enabled = enabled

            def flatten(self) -> bool:
                return False

            def authorize_resume(self) -> bool:
                if self.risk_state in {"DAILY_LOSS_REVIEW", "MAX_DRAWDOWN_REVIEW"}:
                    self.risk_halted = False
                    self.risk_state = "OK"
                return self.accounting_state == "VALID" and not self.risk_halted

            def recheck_integrity(self) -> bool:
                self.rechecks += 1
                return self.accounting_state == "VALID"

            def close(self) -> None:
                self.closed = True

            def snapshot(self) -> PaperSnapshot:
                return PaperSnapshot(
                    portfolio={
                        "equity_usdt": 300.0,
                        "daily_pnl_usdt": 0.0,
                        "drawdown_pct": 0.0,
                        "trades_today": 0,
                        "open_positions": 0,
                        "fees_usdt": 0.0,
                        "slippage_usdt": 0.0,
                    },
                    strategy={
                        "name": "REST Momentum",
                        "symbol": "ETH_USDT",
                        "status": self.strategy_status,
                        "armed": False,
                        "expected_gross_bps": 0.0,
                        "expected_cost_bps": 0.0,
                        "expected_net_bps": 0.0,
                        "last_signal": None,
                    },
                    orders=0,
                    positions=0,
                    risk_halted=self.risk_halted,
                    alerts=[],
                    diagnostics={
                        "accounting": {"state": self.accounting_state, "reason": None},
                        "risk": {
                            "state": self.risk_state,
                            "rollover_review_required": self.risk_state
                            in {"DAILY_LOSS_REVIEW", "MAX_DRAWDOWN_REVIEW"},
                        },
                        "execution_model": {"state": "PAPER_SIM"},
                        "run": {},
                        "storage": {"safe": self.storage_safe},
                        "symbols": {"quarantined": {}},
                    },
                )

        return FakePaper()

    @staticmethod
    def tradingview() -> object:
        class FakeTradingView:
            def snapshot(self) -> dict[str, object]:
                return {
                    "enabled": False,
                    "status": "DISABLED",
                    "advisory_only": True,
                    "execution_influence": "NONE",
                }

            def stop(self) -> None:
                return None

        return FakeTradingView()

    def test_market_ranking_filters_bad_data_and_wide_spreads(self) -> None:
        settings = replace(
            self.settings(),
            active_symbols=5,
            minimum_quote_volume=Decimal(0),
            maximum_spread_bps=Decimal(5),
        )
        payload = [
            {
                "contract": "BTC_USDT",
                "last": "100",
                "highest_bid": "99.99",
                "lowest_ask": "100.01",
                "change_percentage": "2",
                "volume_24h_quote": "10000000000",
                "funding_rate": "0.0001",
            },
            {
                "contract": "MEME_USDT",
                "last": "1",
                "highest_bid": "0.9999",
                "lowest_ask": "1.0001",
                "change_percentage": "300",
                "volume_24h_quote": "5000000",
                "funding_rate": "0",
            },
            {
                "contract": "WIDE_USDT",
                "last": "100",
                "highest_bid": "99",
                "lowest_ask": "101",
                "change_percentage": "10",
                "volume_24h_quote": "20000000",
                "funding_rate": "0",
            },
            {"contract": "BROKEN_USDT", "last": "NaN"},
        ]

        markets = rank_tickers(payload, settings)

        self.assertEqual(
            [market["symbol"] for market in markets],
            ["BTC_USDT", "MEME_USDT", "WIDE_USDT"],
        )
        self.assertTrue(markets[0]["selected"])
        self.assertEqual(markets[2]["rejection"], "WIDE SPREAD")

    def test_gate_contract_metadata_supplies_exact_price_increment(self) -> None:
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            [{"name": "PUMP_USDT", "order_price_round": "0.000001"}]
        ).encode()
        response.__enter__.return_value = response
        with patch("autotrade.dashboard.urlopen", return_value=response):
            increments = fetch_gate_price_increments()
        self.assertEqual(increments, {"PUMP_USDT": "0.000001"})
        settings = replace(self.settings(), minimum_quote_volume=Decimal(0))
        markets = rank_tickers(
            [
                {
                    "contract": "PUMP_USDT",
                    "last": "0.004394",
                    "highest_bid": "0.004392",
                    "lowest_ask": "0.004393",
                    "change_percentage": "1",
                    "volume_24h_quote": "10000000",
                    "funding_rate": "0",
                }
            ],
            settings,
            price_increments=increments,
        )
        self.assertEqual(markets[0]["price_increment"], "0.000001")

    def test_market_ranking_keeps_the_initialized_monitoring_universe(self) -> None:
        settings = replace(
            self.settings(),
            active_symbols=5,
            minimum_quote_volume=Decimal(0),
            maximum_spread_bps=Decimal(5),
        )
        payload = [
            {
                "contract": f"PAIR{index}_USDT",
                "last": "100",
                "highest_bid": "99.99",
                "lowest_ask": "100.01",
                "change_percentage": str(index),
                "volume_24h_quote": "10000000",
                "funding_rate": "0",
            }
            for index in range(1, 7)
        ]

        first = rank_tickers(payload, settings)
        self.assertNotIn("PAIR1_USDT", [market["symbol"] for market in first])

        retained = rank_tickers(payload, settings, ("PAIR1_USDT",))
        self.assertIn("PAIR1_USDT", [market["symbol"] for market in retained])
        self.assertEqual(sum(bool(market["selected"]) for market in retained), 5)

    def test_stale_data_pauses_entries_and_recovers_automatically(self) -> None:
        settings = replace(self.settings(), market_stale_after_seconds=15)
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        paper = self.paper()
        state = DashboardState(
            report,
            settings,
            paper,  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        market: list[dict[str, object]] = [{"selected": True}]

        state.apply_snapshot(market, 10, monotonic_now=100)
        startup = state.snapshot(monotonic_now=100)
        self.assertTrue(startup["controls"]["auto_resume_allowed"])  # type: ignore[index]
        self.assertTrue(state.resume(monotonic_now=100))
        active = state.snapshot(monotonic_now=100)
        self.assertEqual(active["trading_state"], "ACTIVE")
        self.assertIn("open_trade", active)
        self.assertIn("trade_history", active)
        self.assertEqual(state.snapshot(monotonic_now=116)["trading_state"], "PAUSED")

        state.apply_snapshot(market, 10, monotonic_now=117)
        self.assertEqual(state.snapshot(monotonic_now=117)["trading_state"], "ACTIVE")
        state.apply_error(RuntimeError("temporary Gate failure"))
        failed = state.snapshot(monotonic_now=118)
        self.assertEqual(failed["trading_state"], "PAUSED")
        self.assertFalse(failed["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(failed["controls"]["auto_resume_allowed"])  # type: ignore[index]

        state.apply_snapshot(market, 10, monotonic_now=119)
        self.assertEqual(state.snapshot(monotonic_now=119)["trading_state"], "ACTIVE")
        paper.risk_halted = True  # type: ignore[attr-defined]
        halted = state.snapshot(monotonic_now=120)
        self.assertEqual(halted["trading_state"], "HALTED")
        self.assertFalse(halted["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(halted["controls"]["auto_resume_allowed"])  # type: ignore[index]
        self.assertEqual(halted["tradingview"]["status"], "DISABLED")  # type: ignore[index]

    def test_manual_pause_is_never_watchdog_resumable(self) -> None:
        settings = self.settings()
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        state = DashboardState(
            report,
            settings,
            self.paper(),  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        state.apply_snapshot([{"selected": True}], 10, monotonic_now=100)
        self.assertTrue(state.resume(monotonic_now=100))

        state.pause()
        paused = state.snapshot(monotonic_now=100)
        self.assertTrue(paused["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(paused["controls"]["auto_resume_allowed"])  # type: ignore[index]

    def test_daily_loss_rollover_allows_only_manual_resume(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        paper = self.paper()
        paper.risk_halted = True  # type: ignore[attr-defined]
        paper.risk_state = "DAILY_LOSS_REVIEW"  # type: ignore[attr-defined]
        state = DashboardState(
            report,
            self.settings(),
            paper,  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        state.apply_snapshot([{"selected": True}], 10, monotonic_now=100)

        halted = state.snapshot(monotonic_now=100)
        self.assertTrue(halted["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(halted["controls"]["auto_resume_allowed"])  # type: ignore[index]
        self.assertTrue(state.resume(monotonic_now=100))
        self.assertEqual(state.snapshot(monotonic_now=100)["trading_state"], "ACTIVE")

    def test_max_drawdown_rollover_allows_only_manual_resume(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        paper = self.paper()
        paper.risk_halted = True  # type: ignore[attr-defined]
        paper.risk_state = "MAX_DRAWDOWN_REVIEW"  # type: ignore[attr-defined]
        state = DashboardState(
            report,
            self.settings(),
            paper,  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        state.apply_snapshot([{"selected": True}], 10, monotonic_now=100)

        halted = state.snapshot(monotonic_now=100)
        self.assertTrue(halted["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(halted["controls"]["auto_resume_allowed"])  # type: ignore[index]
        self.assertTrue(state.resume(monotonic_now=100))
        self.assertEqual(state.snapshot(monotonic_now=100)["trading_state"], "ACTIVE")

    def test_max_drawdown_can_start_only_a_new_archived_paper_run(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        halted: Any = self.paper()
        halted.risk_halted = True
        halted.risk_state = "MAX_DRAWDOWN"
        replacement: Any = self.paper()
        state = DashboardState(
            report,
            self.settings(),
            halted,
            self.tradingview(),  # type: ignore[arg-type]
            config_path=Path("config/paper.toml"),
            paper_factory=lambda: replacement,
        )
        state.apply_snapshot([{"selected": True}], 10)
        before = state.snapshot()
        self.assertTrue(before["controls"]["new_paper_run_allowed"])  # type: ignore[index]

        metadata = {"run_id": "new-run-id"}
        with patch("autotrade.dashboard.create_new_run", return_value=metadata) as create:
            self.assertEqual(state.start_new_paper_run(), metadata)
        create.assert_called_once()
        self.assertTrue(halted.closed)
        self.assertTrue(replacement.entry_enabled)
        after = state.snapshot()
        self.assertEqual(after["trading_state"], "ACTIVE")
        self.assertFalse(after["controls"]["new_paper_run_allowed"])  # type: ignore[index]

    def test_invalid_accounting_blocks_manual_and_watchdog_resume(self) -> None:
        settings = self.settings()
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        paper = self.paper()
        paper.accounting_state = "INVALID"  # type: ignore[attr-defined]
        state = DashboardState(
            report,
            settings,
            paper,  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        state.apply_snapshot([{"selected": True}], 10, monotonic_now=100)
        snapshot = state.snapshot(monotonic_now=100)
        self.assertFalse(snapshot["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(snapshot["controls"]["auto_resume_allowed"])  # type: ignore[index]
        self.assertFalse(state.resume(monotonic_now=100))

    def test_execution_fault_remains_halted_after_later_good_snapshot(self) -> None:
        settings = self.settings()
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        paper = self.paper()
        state = DashboardState(
            report,
            settings,
            paper,  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        market: list[dict[str, object]] = [{"selected": True}]
        state.apply_snapshot(market, 10, monotonic_now=100)
        self.assertTrue(state.resume(monotonic_now=100))

        good_process = paper.process  # type: ignore[attr-defined]

        def fail_process(markets: object, *, entry_enabled: bool) -> None:
            raise RuntimeError("execution state uncertain")

        paper.process = fail_process  # type: ignore[attr-defined]
        state.apply_snapshot(market, 10, monotonic_now=101)
        paper.process = good_process  # type: ignore[attr-defined]
        state.apply_snapshot(market, 10, monotonic_now=102)

        halted = state.snapshot(monotonic_now=102)
        self.assertEqual(halted["trading_state"], "HALTED")
        self.assertFalse(halted["controls"]["resume_allowed"])  # type: ignore[index]
        self.assertFalse(halted["controls"]["auto_resume_allowed"])  # type: ignore[index]
        self.assertIn("execution state uncertain", halted["alerts"][0])  # type: ignore[index]

    def test_control_requests_require_local_host_and_origin(self) -> None:
        headers = Message()
        headers["Host"] = "127.0.0.1:8765"
        headers["Origin"] = "http://127.0.0.1:8765"
        self.assertTrue(request_is_local(headers, 8765, require_origin=True))

        headers.replace_header("Origin", "https://evil.example")
        self.assertFalse(request_is_local(headers, 8765, require_origin=True))

    def test_analysis_repairs_startup_and_preserves_safety_halts(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        for failure, code in (
            (None, "HEALTHY"),
            ("accounting", "ACCOUNTING_INVALID"),
            ("risk", "RISK_HALT"),
            ("storage", "STORAGE_LOW"),
            ("execution", "EXECUTION_FAULT"),
            ("manual", "MANUAL_PAUSE"),
            ("feed", "FEED_UNHEALTHY"),
            ("position", "RECOVERY_REQUIRED"),
        ):
            with self.subTest(failure=failure):
                paper: Any = self.paper()
                state = DashboardState(
                    report,
                    self.settings(),
                    paper,
                    self.tradingview(),  # type: ignore[arg-type]
                )
                state.apply_snapshot([{"selected": True}], 10)
                if failure == "accounting":
                    paper.accounting_state = "INVALID"
                elif failure == "risk":
                    paper.risk_halted = True
                    paper.risk_state = "DAILY_LOSS_REVIEW"
                elif failure == "position":
                    paper.risk_halted = True
                    paper.strategy_status = "RECOVERY_REQUIRED"
                elif failure == "storage":
                    paper.storage_safe = False
                elif failure == "manual":
                    state.pause()
                elif failure == "feed":
                    state.apply_error(RuntimeError("Gate unavailable"))
                elif failure == "execution":
                    with patch.object(paper, "process", side_effect=RuntimeError("uncertain")):
                        state.apply_snapshot([{"selected": True}], 10)
                result = state.analyze_and_repair("test halt")
                self.assertEqual(result["code"], code)
                self.assertEqual(result["status"], "FIXED" if failure is None else "BLOCKED")
                self.assertEqual(paper.entry_enabled, failure is None)
                if failure is None:
                    self.assertEqual(state.analyze_and_repair("repeat")["status"], "HEALTHY")
                if failure in {"storage", "execution"}:
                    self.assertEqual(paper.rechecks, 0)

    def test_repair_failure_stays_halted_and_is_reported(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        paper: Any = self.paper()
        state = DashboardState(
            report,
            self.settings(),
            paper,
            self.tradingview(),  # type: ignore[arg-type]
        )
        state.apply_snapshot([{"selected": True}], 10)
        with patch.object(paper, "recheck_integrity", side_effect=OSError("disk write failed")):
            result = state.analyze_and_repair("test")
        self.assertEqual(result["code"], "EXECUTION_FAULT")
        self.assertIn("disk write failed", str(result["detail"]))
        self.assertFalse(state.resume())

    def test_repair_endpoint_is_same_origin_and_rejects_concurrent_repairs(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        state = DashboardState(
            report,
            self.settings(),
            self.paper(),  # type: ignore[arg-type]
            self.tradingview(),  # type: ignore[arg-type]
        )
        poller = MagicMock()
        poller.poll_once.side_effect = lambda: state.apply_snapshot([{"selected": True}], 10)
        server = DashboardServer(("127.0.0.1", 0), state, poller, logging.getLogger("test.api"))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(origin + "/api/control/analyze-repair", method="POST")
            with self.assertRaises(HTTPError) as denied:
                urlopen(request, timeout=5)
            self.assertEqual(denied.exception.code, 403)
            poller.poll_once.assert_not_called()
            request.add_header("Origin", origin)
            new_run_request = Request(
                origin + "/api/control/new-paper-run",
                method="POST",
                headers={"Origin": origin},
            )
            with self.assertRaises(HTTPError) as unconfirmed:
                urlopen(new_run_request, timeout=5)
            self.assertEqual(unconfirmed.exception.code, 400)
            with server.repair_lock, self.assertRaises(HTTPError) as busy:
                urlopen(request, timeout=5)
            self.assertEqual(busy.exception.code, 409)
            with urlopen(request, timeout=5) as response:
                result = json.load(response)
            self.assertEqual(result["recovery"]["status"], "FIXED")
            self.assertEqual(result["trading_state"], "ACTIVE")
            poller.poll_once.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
