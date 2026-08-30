from __future__ import annotations

import os
import unittest
from dataclasses import replace
from decimal import Decimal
from email.message import Message
from unittest.mock import patch

from autotrade.config import Settings, load_settings
from autotrade.dashboard import DashboardState, rank_tickers, request_is_local
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

            def process(self, markets: object, *, entry_enabled: bool) -> None:
                self.entry_enabled = entry_enabled

            def set_entry_enabled(self, enabled: bool) -> None:
                self.entry_enabled = enabled

            def flatten(self) -> bool:
                return False

            def close(self) -> None:
                return None

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
                        "status": "PAUSED",
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


if __name__ == "__main__":
    unittest.main()
