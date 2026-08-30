from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import patch

from autotrade.config import load_settings
from autotrade.paper import PaperTrader
from autotrade.runtime import RuntimeReport

SAFE_ENV = {
    "TRADING_MODE": "PAPER",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION": "",
}


def market(price: float) -> list[dict[str, object]]:
    return [
        {
            "symbol": "ETH_USDT",
            "last": price,
            "bid": price - 0.01,
            "ask": price + 0.01,
            "selected": True,
        }
    ]


def tournament_market(eth_price: float, btc_price: float) -> list[dict[str, object]]:
    return [
        {
            "symbol": symbol,
            "last": price,
            "bid": price - 0.01,
            "ask": price + 0.01,
            "selected": True,
        }
        for symbol, price in (("ETH_USDT", eth_price), ("BTC_USDT", btc_price))
    ]


def low_price_market(price: float) -> list[dict[str, object]]:
    return [
        {
            "symbol": "BTR_USDT",
            "last": price,
            "bid": price - 0.00001,
            "ask": price + 0.00001,
            "selected": True,
        }
    ]


class PaperTraderTests(unittest.TestCase):
    def test_initial_coarse_quote_does_not_cross_later_narrow_spread(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(load_settings(), log_directory=Path(directory))
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.precision"))
            try:
                first = [
                    {
                        "symbol": "UNI_USDT",
                        "last": 4.9,
                        "bid": 4.89,
                        "ask": 4.9,
                        "selected": True,
                    }
                ]
                narrow = [
                    {
                        "symbol": "UNI_USDT",
                        "last": 4.8965,
                        "bid": 4.896,
                        "ask": 4.897,
                        "selected": True,
                    }
                ]
                trader.process(first, entry_enabled=False)
                trader.process(narrow, entry_enabled=False)
                snapshot = trader.snapshot()
                self.assertFalse(snapshot.risk_halted)
                self.assertEqual(snapshot.strategy["monitored_symbols"], 1)
            finally:
                trader.close()

    def test_all_screened_pairs_are_monitored_but_only_top_confidence_trades(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_slippage_bps=Decimal("0"),
            )
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.tournament"))
            try:
                eth = (100.00, 100.03, 100.06, 100.09, 100.12, 100.25, 100.26)
                btc = (100.00, 100.05, 100.10, 100.15, 100.20, 100.35, 100.36)
                for eth_price, btc_price in zip(eth, btc, strict=True):
                    trader.process(tournament_market(eth_price, btc_price), entry_enabled=True)

                snapshot = trader.snapshot()
                self.assertEqual(snapshot.strategy["monitored_symbols"], 2)
                self.assertEqual(snapshot.strategy["execution_slots"], 1)
                self.assertEqual(len(cast(list[object], snapshot.strategy["candidates"])), 2)
                self.assertEqual(snapshot.positions, 1)
                self.assertIsNotNone(snapshot.open_trade)
                assert snapshot.open_trade is not None
                self.assertEqual(snapshot.open_trade["symbol"], "BTC_USDT")

                trader.process(tournament_market(100.50, 100.37), entry_enabled=True)
                self.assertEqual(trader.snapshot().positions, 1)
                state = json.loads((Path(directory) / "paper-state.json").read_text())
                self.assertEqual(state["symbol"], "MULTI")
                self.assertEqual(state["position"]["symbol"], "BTC_USDT")
            finally:
                trader.close()

    def test_cost_aware_signal_opens_and_closes_in_nautilus(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_stop_loss_bps=Decimal("20"),
                strategy_take_profit_bps=Decimal("20"),
                strategy_slippage_bps=Decimal("0"),
                strategy_cooldown_seconds=5,
            )
            trader = PaperTrader(report, settings, logging.getLogger("test.paper"))
            try:
                for price in (100.00, 100.05, 100.10, 100.15, 100.20, 100.35, 100.36):
                    trader.process(market(price), entry_enabled=True)
                opened = trader.snapshot()
                self.assertEqual(opened.positions, 1)
                self.assertGreaterEqual(opened.orders, 1)
                open_trade = opened.open_trade
                self.assertIsNotNone(open_trade)
                assert open_trade is not None
                self.assertEqual(open_trade["side"], "LONG")
                self.assertAlmostEqual(cast(float, open_trade["entry_price"]), 100.37)

                for price in (100.70, 100.71):
                    trader.process(market(price), entry_enabled=True)
                closed = trader.snapshot()
                self.assertEqual(closed.positions, 0)
                self.assertEqual(closed.portfolio["trades_today"], 1)
                self.assertEqual(len(closed.trade_history), 1)
                trade = closed.trade_history[0]
                self.assertEqual(trade["side"], "LONG")
                self.assertAlmostEqual(cast(float, trade["open_price"]), 100.37)
                self.assertAlmostEqual(cast(float, trade["close_price"]), 100.69)
                self.assertGreater(cast(float, trade["realized_pnl_usdt"]), 0)
                self.assertGreater(cast(float, trade["pnl_pct"]), 0)
                self.assertGreater(cast(float, trade["fee_usdt"]), 0)
                self.assertTrue((Path(directory) / "paper-events.jsonl").exists())
                state = json.loads((Path(directory) / "paper-state.json").read_text())
                self.assertIsNone(state["position"])
            finally:
                trader.close()

    def test_restart_with_open_position_requires_manual_flatten(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_slippage_bps=Decimal("0"),
            )
            first = PaperTrader(report, settings, logging.getLogger("test.paper.recovery"))
            for price in (100.00, 100.05, 100.10, 100.15, 100.20, 100.35, 100.36):
                first.process(market(price), entry_enabled=True)
            self.assertEqual(first.snapshot().positions, 1)
            first.close()

            recovered = PaperTrader(report, settings, logging.getLogger("test.paper.recovered"))
            try:
                recovered.process(market(100.40), entry_enabled=False)
                blocked = recovered.snapshot()
                self.assertTrue(blocked.risk_halted)
                self.assertEqual(blocked.strategy["status"], "RECOVERY_REQUIRED")
                self.assertTrue(recovered.flatten())
                flattened = recovered.snapshot()
                self.assertFalse(flattened.risk_halted)
                self.assertEqual(flattened.positions, 0)
            finally:
                recovered.close()

    def test_entry_requires_persistent_move_and_matching_regime(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_slippage_bps=Decimal("0"),
            )
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.confirmation"))
            try:
                for price in (100.50, 100.40, 100.30, 100.00, 100.01, 100.20, 100.30):
                    trader.process(market(price), entry_enabled=True)
                snapshot = trader.snapshot()
                self.assertEqual(snapshot.positions, 0)
                self.assertEqual(snapshot.strategy["status"], "WAITING_CONFIRMATION")
            finally:
                trader.close()

    def test_entry_requires_minimum_net_edge_and_confidence(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        prices = (100.00, 100.10, 100.20, 100.30, 100.40, 100.50, 100.60)
        cases = (
            (Decimal("18"), Decimal("0.50")),
            (Decimal("5"), Decimal("0.80")),
        )
        for minimum_net_edge, minimum_confidence in cases:
            with (
                self.subTest(
                    minimum_net_edge=minimum_net_edge,
                    minimum_confidence=minimum_confidence,
                ),
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, SAFE_ENV),
            ):
                settings = replace(
                    load_settings(),
                    log_directory=Path(directory),
                    strategy_window=3,
                    strategy_persistence_ticks=2,
                    strategy_regime_window=6,
                    strategy_entry_threshold_bps=Decimal("20"),
                    strategy_minimum_net_edge_bps=minimum_net_edge,
                    strategy_minimum_confidence=minimum_confidence,
                    strategy_slippage_bps=Decimal("0"),
                )
                trader = PaperTrader(report, settings, logging.getLogger("test.paper.edge"))
                try:
                    for price in prices:
                        trader.process(market(price), entry_enabled=True)
                    snapshot = trader.snapshot()
                    self.assertEqual(snapshot.positions, 0)
                    self.assertEqual(snapshot.strategy["status"], "WAITING_EDGE")
                finally:
                    trader.close()

    def test_regime_confirmation_rejects_late_reversal_burst(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_slippage_bps=Decimal("0"),
            )
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.regime"))
            try:
                for price in (100.00, 99.95, 99.90, 99.85, 99.95, 100.15, 100.30):
                    trader.process(market(price), entry_enabled=True)
                snapshot = trader.snapshot()
                self.assertEqual(snapshot.positions, 0)
                self.assertEqual(snapshot.strategy["status"], "WAITING_CONFIRMATION")
            finally:
                trader.close()

    def test_split_fills_are_aggregated_in_trade_history(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_stop_loss_bps=Decimal("20"),
                strategy_take_profit_bps=Decimal("20"),
                strategy_slippage_bps=Decimal("0"),
                strategy_cooldown_seconds=5,
            )
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.split"))
            try:
                for price in (
                    0.10000,
                    0.10010,
                    0.10020,
                    0.10030,
                    0.10040,
                    0.10055,
                    0.10070,
                ):
                    trader.process(low_price_market(price), entry_enabled=True)
                opened = trader.snapshot()
                self.assertEqual(opened.positions, 1)
                assert opened.open_trade is not None
                opened_quantity = cast(float, opened.open_trade["quantity"])
                self.assertGreater(opened_quantity, 100)

                for price in (0.10120, 0.10121):
                    trader.process(low_price_market(price), entry_enabled=True)
                closed = trader.snapshot()
                self.assertEqual(closed.positions, 0)
                trade = closed.trade_history[0]
                quantity = cast(float, trade["quantity"])
                open_price = cast(float, trade["open_price"])
                realized = cast(float, trade["realized_pnl_usdt"])
                self.assertAlmostEqual(quantity, opened_quantity, places=6)
                self.assertAlmostEqual(
                    cast(float, trade["fee_usdt"]),
                    cast(float, closed.portfolio["fees_usdt"]),
                    places=8,
                )
                self.assertAlmostEqual(
                    cast(float, trade["pnl_pct"]),
                    realized / (quantity * open_price) * 100,
                    places=8,
                )
                events = [
                    json.loads(line)
                    for line in (Path(directory) / "paper-events.jsonl").read_text().splitlines()
                ]
                self.assertGreaterEqual(sum(event["event"] == "fill" for event in events), 4)
            finally:
                trader.close()

    def test_legacy_split_fill_history_is_reconstructed_from_audit_events(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        now = datetime.now(UTC).isoformat()
        events = [
            {"event": "signal", "timestamp_utc": now, "symbol": "BTR_USDT"},
            {
                "event": "fill",
                "timestamp_utc": now,
                "symbol": "BTR_USDT",
                "quantity": 100,
                "price": 0.100,
                "fee_usdt": 0.005,
            },
            {"event": "position_opened", "timestamp_utc": now, "symbol": "BTR_USDT"},
            {
                "event": "fill",
                "timestamp_utc": now,
                "symbol": "BTR_USDT",
                "quantity": 200,
                "price": 0.101,
                "fee_usdt": 0.0101,
            },
            {"event": "exit_signal", "timestamp_utc": now, "symbol": "BTR_USDT"},
            {
                "event": "fill",
                "timestamp_utc": now,
                "symbol": "BTR_USDT",
                "quantity": 100,
                "price": 0.099,
                "fee_usdt": 0.00495,
            },
            {
                "event": "fill",
                "timestamp_utc": now,
                "symbol": "BTR_USDT",
                "quantity": 200,
                "price": 0.098,
                "fee_usdt": 0.0098,
            },
            {
                "event": "position_closed",
                "timestamp_utc": now,
                "symbol": "BTR_USDT",
                "side": "LONG",
                "quantity": 100,
                "open_price": 0.100,
                "close_price": 0.098,
                "realized_pnl_usdt": -0.72985,
                "pnl_pct": -7.2985,
                "fee_usdt": 0.0148,
                "reason": "STOP_LOSS",
            },
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory) / "paper-events.jsonl"
            path.write_text(
                "\n".join(json.dumps(event) for event in events),
                encoding="utf-8",
            )
            settings = replace(load_settings(), log_directory=Path(directory))
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.legacy-split"))
            try:
                trade = trader.snapshot().trade_history[0]
                self.assertAlmostEqual(cast(float, trade["quantity"]), 300.0)
                self.assertAlmostEqual(cast(float, trade["open_price"]), 30.2 / 300)
                self.assertAlmostEqual(cast(float, trade["close_price"]), 29.5 / 300)
                self.assertAlmostEqual(cast(float, trade["fee_usdt"]), 0.02985)
                self.assertAlmostEqual(
                    cast(float, trade["pnl_pct"]),
                    -0.72985 / 30.2 * 100,
                )
            finally:
                trader.close()

    def test_trade_history_is_limited_to_timestamped_last_48_hours(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        now = datetime.now(UTC)
        base = {
            "event": "position_closed",
            "symbol": "ETH_USDT",
            "side": "LONG",
            "quantity": 0.01,
            "open_price": 100.0,
            "close_price": 101.0,
            "realized_pnl_usdt": 0.009,
            "pnl_pct": 0.9,
            "fee_usdt": 0.001,
            "reason": "TAKE_PROFIT",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory) / "paper-events.jsonl"
            events = [
                {**base, "timestamp_utc": (now - timedelta(hours=47)).isoformat()},
                {**base, "timestamp_utc": (now - timedelta(hours=49)).isoformat()},
                {"event": "position_closed", "timestamp_utc": now.isoformat()},
            ]
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            settings = replace(load_settings(), log_directory=Path(directory))
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.history"))
            try:
                history = trader.snapshot().trade_history
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["reason"], "TAKE_PROFIT")
            finally:
                trader.close()

    def test_legacy_state_migrates_lifetime_pnl_to_current_utc_day(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        now = datetime.now(UTC)
        base = {
            "event": "position_closed",
            "symbol": "ETH_USDT",
            "side": "LONG",
            "quantity": 1,
            "open_price": 100,
            "close_price": 95,
            "pnl_pct": -5,
            "fee_usdt": 0,
            "reason": "STOP_LOSS",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory)
            events = [
                {
                    **base,
                    "timestamp_utc": now.isoformat(),
                    "realized_pnl_usdt": -5,
                },
                {
                    **base,
                    "timestamp_utc": (now - timedelta(days=1)).isoformat(),
                    "realized_pnl_usdt": -2,
                },
            ]
            (path / "paper-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )
            (path / "paper-state.json").write_text(
                json.dumps(
                    {
                        "mode": "PAPER",
                        "symbol": "MULTI",
                        "balance_usdt": "293",
                        "trades": 2,
                        "fees_usdt": "0",
                        "position": None,
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(load_settings(), log_directory=path)
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.daily-migrate"))
            try:
                snapshot = trader.snapshot()
                self.assertAlmostEqual(snapshot.portfolio["daily_pnl_usdt"], -5.0)
                self.assertEqual(snapshot.portfolio["trades_today"], 1)
                self.assertFalse(snapshot.risk_halted)
            finally:
                trader.close()

    def test_daily_risk_halt_is_persisted_for_current_utc_day(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        today = datetime.now(UTC).date().isoformat()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory)
            (path / "paper-state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "mode": "PAPER",
                        "symbol": "MULTI",
                        "balance_usdt": "293",
                        "trades": 1,
                        "fees_usdt": "0.1",
                        "position": None,
                        "risk_day_utc": today,
                        "day_start_equity_usdt": "300",
                        "trades_at_day_start": 0,
                        "peak_equity_usdt": "300",
                        "risk_halted": True,
                        "risk_halt_reason": "DAILY_LOSS",
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(load_settings(), log_directory=path)
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.risk-persist"))
            try:
                snapshot = trader.snapshot()
                self.assertTrue(snapshot.risk_halted)
                self.assertAlmostEqual(snapshot.portfolio["daily_pnl_usdt"], -7.0)
                self.assertIn("DAILY_LOSS", snapshot.alerts[0])
            finally:
                trader.close()

    def test_recovery_flatten_rejects_stale_cached_quote(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory)
            (path / "paper-state.json").write_text(
                json.dumps(
                    {
                        "mode": "PAPER",
                        "symbol": "MULTI",
                        "balance_usdt": "300",
                        "trades": 0,
                        "fees_usdt": "0",
                        "position": {
                            "symbol": "ETH_USDT",
                            "side": "LONG",
                            "quantity": 0.1,
                            "entry_price": 100,
                            "entry_fee_usdt": 0.005,
                        },
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(load_settings(), log_directory=path, market_stale_after_seconds=15)
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.stale-flatten"))
            try:
                with patch("autotrade.paper.monotonic", side_effect=[100.0, 116.0]):
                    trader.process(market(100), entry_enabled=False)
                    with self.assertRaisesRegex(RuntimeError, "no fresh market"):
                        trader.flatten()
            finally:
                trader.close()


if __name__ == "__main__":
    unittest.main()
