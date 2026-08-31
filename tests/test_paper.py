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
from autotrade.integrity import EventLedger, create_new_run, new_identity, utc_now
from autotrade.paper import PaperTrader
from autotrade.runtime import RuntimeReport

SAFE_ENV = {
    "TRADING_MODE": "PAPER",
    "LIVE_TRADING_ENABLED": "false",
    "LIVE_CONFIRMATION": "",
}


def current_events_path(root: Path) -> Path:
    state = json.loads((root / "paper-state.json").read_text(encoding="utf-8"))
    return root / str(state["event_ledger_path"])


def append_closed_trade(
    ledger: EventLedger,
    realized: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
) -> None:
    timestamp = utc_now()
    signal_id = new_identity()
    position_id = new_identity()
    entry_order_id = new_identity()
    exit_order_id = new_identity()
    ledger.append(
        "signal",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        side="LONG",
        decision_timestamp=timestamp,
    )
    ledger.append(
        "order_submitted",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        position_id=position_id,
        order_id=entry_order_id,
        client_order_id=new_identity(),
        side="BUY",
        order_type="MARKET",
        quantity="1",
        order_submit_timestamp=timestamp,
    )
    ledger.append(
        "fill",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        position_id=position_id,
        order_id=entry_order_id,
        client_order_id=new_identity(),
        fill_id=new_identity(),
        side="BUY",
        quantity="1",
        price="100",
        fee_amount=str(entry_fee),
        fee_currency="USDT",
        fill_timestamp=timestamp,
    )
    ledger.append(
        "position_opened",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        position_id=position_id,
        side="LONG",
        quantity="1",
        price="100",
    )
    ledger.append(
        "exit_signal",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        position_id=position_id,
        order_id=exit_order_id,
        side="LONG",
        reason="TIME_EXIT",
        decision_timestamp=timestamp,
    )
    ledger.append(
        "fill",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        position_id=position_id,
        order_id=exit_order_id,
        client_order_id=new_identity(),
        fill_id=new_identity(),
        side="SELL",
        quantity="1",
        price="99",
        fee_amount=str(exit_fee),
        fee_currency="USDT",
        fill_timestamp=timestamp,
    )
    ledger.append(
        "position_closed",
        symbol="ETH_USDT",
        strategy_id="TEST",
        signal_id=signal_id,
        position_id=position_id,
        side="LONG",
        quantity="1",
        price="99",
        fee_amount=str(entry_fee + exit_fee),
        fee_currency="USDT",
        reason="TIME_EXIT",
        realized_pnl_usdt=str(realized),
        open_price="100",
        close_price="99",
        pnl_pct=str(realized),
    )


def market(price: float) -> list[dict[str, object]]:
    return [
        {
            "symbol": "ETH_USDT",
            "last": price,
            "bid": price - 0.01,
            "ask": price + 0.01,
            "price_increment": "0.0001",
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
            "price_increment": "0.0001",
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
            "price_increment": "0.000001",
            "selected": True,
        }
    ]


class PaperTraderTests(unittest.TestCase):
    def test_missing_checkpoint_with_prior_run_never_creates_replacement_run(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            root = Path(directory)
            settings = replace(load_settings(), log_directory=root)
            first = PaperTrader(report, settings, logging.getLogger("test.paper.first-run"))
            first_run = cast(dict[str, object], first.snapshot().diagnostics["run"])["run_id"]
            first.close()
            (root / "paper-state.json").unlink()

            recovered = PaperTrader(report, settings, logging.getLogger("test.paper.missing-state"))
            try:
                snapshot = recovered.snapshot()
                accounting = cast(dict[str, object], snapshot.diagnostics["accounting"])
                self.assertEqual(accounting["state"], "INVALID")
                self.assertIn("checkpoint is missing", str(accounting["reason"]))
                self.assertIsNone(cast(dict[str, object], snapshot.diagnostics["run"])["run_id"])
                metadata = list((root / "data" / "runs").glob("*/metadata.json"))
                self.assertEqual(len(metadata), 1)
                self.assertEqual(json.loads(metadata[0].read_text())["run_id"], first_run)
            finally:
                recovered.close()

    def test_invalid_quote_quarantines_only_one_symbol(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(load_settings(), log_directory=Path(directory), active_symbols=2)
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.quarantine"))
            try:
                trader.process(tournament_market(100, 200), entry_enabled=False)
                broken = tournament_market(100, 200.1)
                broken[0]["bid"] = 101
                broken[0]["ask"] = 100
                trader.process(broken, entry_enabled=False)
                snapshot = trader.snapshot()
                self.assertFalse(snapshot.risk_halted)
                accounting = cast(dict[str, object], snapshot.diagnostics["accounting"])
                symbols = cast(dict[str, object], snapshot.diagnostics["symbols"])
                quarantined = cast(dict[str, str], symbols["quarantined"])
                self.assertEqual(accounting["state"], "VALID")
                self.assertIn("ETH_USDT", quarantined)
                self.assertNotIn("BTC_USDT", quarantined)

                trader.process(tournament_market(100.1, 200.2), entry_enabled=False)
                recovered = cast(
                    dict[str, object], trader.snapshot().diagnostics["symbols"]
                )
                self.assertEqual(recovered["quarantined"], {})
            finally:
                trader.close()

    def test_initial_coarse_quote_does_not_cross_later_narrow_spread(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(load_settings(), log_directory=Path(directory))
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.precision"))
            try:
                first = [
                    {
                        "symbol": "PUMP_USDT",
                        "last": 0.0044,
                        "bid": 0.0043,
                        "ask": 0.0044,
                        "price_increment": "0.000001",
                        "selected": True,
                    }
                ]
                narrow = [
                    {
                        "symbol": "PUMP_USDT",
                        "last": 0.004394,
                        "bid": 0.004392,
                        "ask": 0.004393,
                        "price_increment": "0.000001",
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
                events_path = current_events_path(Path(directory))
                self.assertTrue(events_path.exists())
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                self.assertTrue(all(event["schema_version"] == 3 for event in events))
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
                    for line in current_events_path(Path(directory)).read_text().splitlines()
                ]
                self.assertGreaterEqual(sum(event["event_type"] == "fill" for event in events), 4)
                self.assertEqual(
                    [event["sequence"] for event in events],
                    list(range(1, len(events) + 1)),
                )
                self.assertEqual(len({event["event_id"] for event in events}), len(events))
                lifecycle = [
                    event
                    for event in events
                    if event["event_type"]
                    in {"fill", "position_opened", "exit_signal", "position_closed"}
                ]
                self.assertEqual(len({event["position_id"] for event in lifecycle}), 1)
                closed_event = next(
                    event for event in events if event["event_type"] == "position_closed"
                )
                self.assertIsNotNone(closed_event["position_open_timestamp"])
                self.assertIsNotNone(closed_event["position_close_timestamp"])
                self.assertGreaterEqual(closed_event["holding_time_ms"], 0)
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
                self.assertTrue(snapshot.risk_halted)
                self.assertEqual(snapshot.strategy["status"], "STATE_INVALID")
                accounting = cast(dict[str, object], snapshot.diagnostics["accounting"])
                self.assertEqual(accounting["state"], "INVALID")
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
                self.assertEqual(snapshot.strategy["status"], "STATE_INVALID")
                self.assertIn("legacy/unattributed", snapshot.alerts[-1])
            finally:
                trader.close()

    def test_valid_utc_rollover_is_transactional_and_keeps_pnl_sign(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory)
            settings = replace(load_settings(), log_directory=path)
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.valid-rollover"))
            try:
                trader.process(market(2500), entry_enabled=False)
                trader._risk_day_utc = yesterday
                trader._day_start_equity = Decimal("301")
                trader.process(market(2500.1), entry_enabled=False)
                snapshot = trader.snapshot()
                self.assertFalse(snapshot.risk_halted)
                self.assertAlmostEqual(snapshot.portfolio["daily_pnl_usdt"], 0.0)
                state = json.loads((path / "paper-state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["risk_day_utc"], datetime.now(UTC).date().isoformat())
                self.assertGreaterEqual(state["checkpoint_sequence"], 3)
            finally:
                trader.close()

    def test_daily_loss_rollover_requires_manual_review_before_resume(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            settings = replace(load_settings(), log_directory=Path(directory))
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.loss-review"))
            try:
                trader.process(market(2500), entry_enabled=False)
                trader._risk_day_utc = yesterday
                trader._risk_halted = True
                trader._risk_halt_reason = "DAILY_LOSS"
                trader.process(market(2500.1), entry_enabled=True)
                blocked = trader.snapshot()
                self.assertTrue(blocked.risk_halted)
                risk = cast(dict[str, object], blocked.diagnostics["risk"])
                self.assertEqual(risk["state"], "DAILY_LOSS_REVIEW")
                self.assertTrue(risk["rollover_review_required"])
                self.assertTrue(trader.authorize_resume())
                self.assertFalse(trader.snapshot().risk_halted)
            finally:
                trader.close()

    def test_new_utc_day_restores_persisted_balance_without_resetting_equity(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            path = Path(directory)
            (path / "paper-state.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "mode": "PAPER",
                        "symbol": "MULTI",
                        "balance_usdt": "292.99226546",
                        "trades": 51,
                        "fees_usdt": "1.5430654791597",
                        "position": None,
                        "risk_day_utc": yesterday,
                        "day_start_equity_usdt": "299.0220726297597",
                        "trades_at_day_start": 14,
                        "peak_equity_usdt": "300",
                        "risk_halted": True,
                        "risk_halt_reason": "DAILY_LOSS",
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(load_settings(), log_directory=path)
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.rollover"))
            try:
                trader.process(market(2500), entry_enabled=False)
                snapshot = trader.snapshot()
                self.assertTrue(snapshot.risk_halted)
                self.assertAlmostEqual(snapshot.portfolio["equity_usdt"], 292.99226546)
                self.assertLess(snapshot.portfolio["daily_pnl_usdt"], 0.0)
                self.assertEqual(snapshot.strategy["status"], "STATE_INVALID")
                accounting = cast(dict[str, object], snapshot.diagnostics["accounting"])
                self.assertEqual(accounting["state"], "INVALID")
            finally:
                trader.close()

    def test_v3_restart_uses_292_checkpoint_and_preserves_sticky_daily_loss(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        target_balance = Decimal("292.99226546")
        target_realized = target_balance - Decimal("300")
        target_fees = Decimal("1.5430654791597")
        today = datetime.now(UTC).date().isoformat()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SAFE_ENV):
            root = Path(directory)
            settings = replace(load_settings(), log_directory=root)
            metadata = create_new_run(
                project_root=root,
                config_path=Path(__file__).resolve().parents[1] / "config" / "paper.toml",
                log_directory=root,
                starting_equity=Decimal("300"),
            )
            state_path = root / "paper-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            original_session = state["session_id"]
            ledger = EventLedger(
                root / str(state["event_ledger_path"]),
                str(metadata["run_id"]),
                new_identity(),
            )
            remaining_realized = target_realized
            remaining_fees = target_fees
            for index in range(51):
                realized = (
                    remaining_realized
                    if index == 50
                    else (target_realized / 51).quantize(Decimal("0.0000000001"))
                )
                fees = (
                    remaining_fees
                    if index == 50
                    else (target_fees / 51).quantize(Decimal("0.0000000000001"))
                )
                append_closed_trade(ledger, realized, fees / 2, fees - fees / 2)
                remaining_realized -= realized
                remaining_fees -= fees
            state.update(
                {
                    "checkpoint_sequence": 9,
                    "last_event_sequence": ledger.last_sequence,
                    "balance_usdt": str(target_balance),
                    "cumulative_realized_pnl_usdt": str(target_realized),
                    "trades": 51,
                    "fees_usdt": str(target_fees),
                    "risk_day_utc": today,
                    "day_start_equity_usdt": "300",
                    "trades_at_day_start": 0,
                    "risk_halted": True,
                    "risk_halt_reason": "DAILY_LOSS",
                }
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")

            trader = PaperTrader(report, settings, logging.getLogger("test.paper.v3-restart"))
            try:
                trader.process(market(2500), entry_enabled=True)
                snapshot = trader.snapshot()
                accounting = cast(dict[str, object], snapshot.diagnostics["accounting"])
                run = cast(dict[str, object], snapshot.diagnostics["run"])
                self.assertEqual(accounting["state"], "VALID")
                self.assertAlmostEqual(snapshot.portfolio["equity_usdt"], 292.99226546)
                self.assertAlmostEqual(snapshot.portfolio["daily_pnl_usdt"], -7.00773454)
                self.assertEqual(accounting["trade_count"], 51)
                self.assertTrue(snapshot.risk_halted)
                self.assertEqual(run["run_id"], metadata["run_id"])
                self.assertNotEqual(run["session_id"], original_session)
            finally:
                trader.close()

    def test_automatic_loss_halt_is_labeled_risk_flatten(self) -> None:
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
                strategy_daily_loss_usdt=Decimal("0.01"),
            )
            trader = PaperTrader(report, settings, logging.getLogger("test.paper.risk-flatten"))
            try:
                for price in (100.00, 100.05, 100.10, 100.15, 100.20, 100.35, 100.36):
                    trader.process(market(price), entry_enabled=True)
                trader.process(market(100.36), entry_enabled=False)
                snapshot = trader.snapshot()
                self.assertTrue(snapshot.risk_halted)
                self.assertEqual(snapshot.positions, 0)
                self.assertEqual(snapshot.trade_history[0]["reason"], "RISK_FLATTEN")
            finally:
                trader.close()

    def test_legacy_recovery_cannot_flatten_without_valid_accounting(self) -> None:
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
                    self.assertFalse(trader.flatten())
            finally:
                trader.close()


if __name__ == "__main__":
    unittest.main()
