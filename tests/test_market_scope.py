from __future__ import annotations

import logging
import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from autotrade.analytics import aggregate_trades
from autotrade.config import load_settings
from autotrade.dashboard import rank_tickers
from autotrade.market_scope import (
    FeedHealth,
    MarketScope,
    MarketScopeController,
    SwitchPolicy,
    SwitchState,
    XauDirection,
    XauSettings,
    XauSignalEngine,
)
from autotrade.paper import PaperTrader
from autotrade.runtime import RuntimeReport


def market(symbol: str, price: Decimal, spread_bps: Decimal = Decimal("1")) -> dict[str, object]:
    half_spread = price * spread_bps / Decimal(20_000)
    return {
        "symbol": symbol,
        "last": str(price),
        "bid": str(price - half_spread),
        "ask": str(price + half_spread),
        "spread_bps": str(spread_bps),
    }


class MarketScopeControllerTests(unittest.TestCase):
    def test_old_install_defaults_to_wide_and_persists_immediate_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = MarketScopeController(root / "scope.json", root / "events.jsonl")
            self.assertEqual(controller.active_scope, MarketScope.WIDE_CRYPTO)
            controller.request(
                MarketScope.XAU_ONLY,
                SwitchPolicy.SWITCH_NOW_KEEP_EXISTING,
                open_positions=1,
            )
            restarted = MarketScopeController(root / "scope.json", root / "events.jsonl")
            self.assertEqual(restarted.active_scope, MarketScope.XAU_ONLY)
            self.assertEqual(restarted.entry_scope, MarketScope.XAU_ONLY)

    def test_switch_when_flat_drains_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = MarketScopeController(root / "scope.json", root / "events.jsonl")
            record = controller.request(
                MarketScope.XAU_ONLY,
                SwitchPolicy.SWITCH_WHEN_FLAT,
                open_positions=1,
            )
            self.assertEqual(record.switch_status, SwitchState.DRAINING)
            self.assertIsNone(controller.entry_scope)
            restarted = MarketScopeController(root / "scope.json", root / "events.jsonl")
            self.assertEqual(restarted.snapshot(1)["switch_status"], "DRAINING")
            restarted.reconcile(open_positions=0, accounting_valid=True)
            self.assertEqual(restarted.entry_scope, MarketScope.XAU_ONLY)

    def test_switch_when_flat_activates_immediately_when_already_flat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = MarketScopeController(root / "scope.json", root / "events.jsonl")
            record = controller.request(
                MarketScope.XAU_ONLY,
                SwitchPolicy.SWITCH_WHEN_FLAT,
                open_positions=0,
            )
            self.assertEqual(record.switch_status, SwitchState.ACTIVE)
            self.assertEqual(controller.entry_scope, MarketScope.XAU_ONLY)

    def test_flatten_requires_confirmation_and_conflicts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = MarketScopeController(root / "scope.json", root / "events.jsonl")
            with self.assertRaises(ValueError):
                controller.request(
                    MarketScope.XAU_ONLY,
                    SwitchPolicy.FLATTEN_AND_SWITCH,
                    open_positions=1,
                )
            controller.request(
                MarketScope.XAU_ONLY,
                SwitchPolicy.FLATTEN_AND_SWITCH,
                open_positions=1,
                confirm_flatten=True,
            )
            with self.assertRaises(RuntimeError):
                controller.request(
                    MarketScope.XAU_ONLY,
                    SwitchPolicy.SWITCH_NOW_KEEP_EXISTING,
                    open_positions=1,
                )

    def test_persistence_failure_blocks_entries_and_keeps_old_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = MarketScopeController(root / "scope.json", root / "events.jsonl")
            with (
                patch("autotrade.market_scope.os.replace", side_effect=OSError("disk failed")),
                self.assertRaises(RuntimeError),
            ):
                controller.request(
                    MarketScope.XAU_ONLY,
                    SwitchPolicy.SWITCH_NOW_KEEP_EXISTING,
                    open_positions=0,
                )
            self.assertEqual(controller.active_scope, MarketScope.WIDE_CRYPTO)
            self.assertIsNone(controller.entry_scope)
            self.assertEqual(controller.snapshot()["switch_status"], "FAILED")
            restarted = MarketScopeController(root / "scope.json", root / "events.jsonl")
            self.assertEqual(restarted.active_scope, MarketScope.WIDE_CRYPTO)
            self.assertIsNone(restarted.entry_scope)
            self.assertEqual(restarted.snapshot()["switch_status"], "FAILED")


class XauSignalTests(unittest.TestCase):
    def feed(
        self,
        primary_steps: list[Decimal],
        xaut_steps: list[Decimal] | None = None,
        paxg_steps: list[Decimal] | None = None,
    ) -> XauSignalEngine:
        settings = XauSettings(trend_window=4, short_window=2)
        engine = XauSignalEngine(settings)
        xaut_steps = xaut_steps if xaut_steps is not None else primary_steps
        paxg_steps = paxg_steps if paxg_steps is not None else primary_steps
        for index, primary in enumerate(primary_steps):
            markets = {"XAU_USDT": market("XAU_USDT", primary)}
            if index < len(xaut_steps):
                markets["XAUT_USDT"] = market("XAUT_USDT", xaut_steps[index])
            if index < len(paxg_steps):
                markets["PAXG_USDT"] = market("PAXG_USDT", paxg_steps[index])
            engine.update(markets, now=float(index))
        return engine

    def test_long_short_and_wait(self) -> None:
        long_engine = self.feed([Decimal(value) for value in (100, 101, 102, 103, 104)])
        self.assertEqual(long_engine.last_decision.direction, XauDirection.LONG)
        short_engine = self.feed([Decimal(value) for value in (104, 103, 102, 101, 100)])
        self.assertEqual(short_engine.last_decision.direction, XauDirection.SHORT)
        wait_engine = self.feed([Decimal(value) for value in (100, 100, 100, 100, 100)])
        self.assertEqual(wait_engine.last_decision.direction, XauDirection.WAIT)

    def test_confirmation_disagreement_waits(self) -> None:
        engine = self.feed(
            [Decimal(value) for value in (100, 101, 102, 103, 104)],
            [Decimal(value) for value in (104, 103, 102, 101, 100)],
            [Decimal(value) for value in (104, 103, 102, 101, 100)],
        )
        self.assertEqual(engine.last_decision.direction, XauDirection.WAIT)
        self.assertIn("ABNORMAL_DIVERGENCE", engine.last_decision.reasons)

    def test_missing_confirmation_is_degraded_but_primary_can_trade(self) -> None:
        settings = XauSettings(trend_window=4, short_window=2)
        engine = XauSignalEngine(settings)
        for index, price in enumerate((100, 101, 102, 103, 104)):
            engine.update(
                {"XAU_USDT": market("XAU_USDT", Decimal(price))},
                now=float(index),
            )
        self.assertEqual(engine.last_decision.direction, XauDirection.LONG)
        self.assertEqual(
            engine.last_decision.feed_health["XAUT_USDT"], FeedHealth.UNAVAILABLE.value
        )
        self.assertLess(engine.last_decision.size_factor, Decimal(1))

    def test_one_missing_confirmation_is_degraded_without_blocking(self) -> None:
        settings = XauSettings(trend_window=4, short_window=2)
        engine = XauSignalEngine(settings)
        for index, price in enumerate((100, 101, 102, 103, 104)):
            value = Decimal(price)
            engine.update(
                {
                    "XAU_USDT": market("XAU_USDT", value),
                    "XAUT_USDT": market("XAUT_USDT", value),
                },
                now=float(index),
            )
        self.assertEqual(engine.last_decision.direction, XauDirection.LONG)
        self.assertEqual(
            engine.last_decision.feed_health["PAXG_USDT"],
            FeedHealth.UNAVAILABLE.value,
        )
        self.assertEqual(engine.last_decision.confirmation, "AGREE")
        self.assertLess(engine.last_decision.size_factor, Decimal(1))

    def test_stale_primary_blocks_entry(self) -> None:
        engine = self.feed([Decimal(value) for value in (100, 101, 102, 103, 104)])
        decision = engine.update({}, now=30)
        self.assertEqual(decision.direction, XauDirection.WAIT)
        self.assertEqual(decision.regime.value, "UNTRADABLE")


class MarketScopeIntegrationTests(unittest.TestCase):
    @staticmethod
    def xau_snapshot(xau_price: Decimal, btc_price: Decimal) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for symbol, value in (
            ("BTC_USDT", btc_price),
            ("XAU_USDT", xau_price),
            ("XAUT_USDT", xau_price),
            ("PAXG_USDT", xau_price),
        ):
            row = market(symbol, value)
            row.update(
                price_increment="0.01",
                selected=symbol == "BTC_USDT",
                contract_enabled=True,
                quantity_increment="0.0001",
                minimum_quantity="0.0001",
                leverage_min="1",
                leverage_max="100",
            )
            rows.append(row)
        return rows

    def test_xau_scope_blocks_crypto_and_routes_valid_xau_to_shared_pipeline(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        safe_environment = {
            "TRADING_MODE": "PAPER",
            "LIVE_TRADING_ENABLED": "false",
            "LIVE_CONFIRMATION": "",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, safe_environment
        ):
            settings = load_settings()
            settings = replace(
                settings,
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                xau=replace(
                    settings.xau,
                    trend_window=4,
                    short_window=2,
                    minimum_confidence=Decimal("0.10"),
                ),
            )
            controller = MarketScopeController(
                Path(directory) / "scope.json",
                Path(directory) / "scope-events.jsonl",
                MarketScope.XAU_ONLY,
            )
            trader = PaperTrader(
                report,
                settings,
                logging.getLogger("test.market-scope.xau"),
                controller,
            )
            try:
                for index in range(7):
                    trader.process(
                        self.xau_snapshot(Decimal(100), Decimal(100 + index)),
                        entry_enabled=True,
                    )
                xau_strategy = trader._strategies["XAU_USDT"]
                self.assertEqual(xau_strategy.config.size_increment, Decimal("0.0001"))
                self.assertEqual(xau_strategy.config.size_precision, 4)
                self.assertIn("BTC_USDT", trader.shadow_symbols)
                self.assertNotIn("XAU_USDT", trader.shadow_symbols)
                self.assertNotIn("XAUT_USDT", trader.shadow_symbols)
                self.assertNotIn("PAXG_USDT", trader.shadow_symbols)
                self.assertEqual(trader.snapshot().positions, 0)
                for index in range(1, 7):
                    trader.process(
                        self.xau_snapshot(Decimal(100 + index), Decimal(110)),
                        entry_enabled=True,
                    )
                opened = trader.snapshot()
                self.assertEqual(opened.positions, 1)
                self.assertEqual(opened.open_trade["symbol"], "XAU_USDT")  # type: ignore[index]
            finally:
                trader.close()

    def test_switch_when_flat_keeps_managing_crypto_then_activates_xau(self) -> None:
        report = RuntimeReport("PAPER", "test", "TESTER-001", "GATE", "300", "1", True, True)
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                load_settings(),
                log_directory=Path(directory),
                strategy_window=3,
                strategy_persistence_ticks=2,
                strategy_regime_window=6,
                strategy_entry_threshold_bps=Decimal("10"),
                strategy_minimum_net_edge_bps=Decimal("1"),
                strategy_minimum_confidence=Decimal("0.10"),
                strategy_take_profit_bps=Decimal("20"),
                strategy_slippage_bps=Decimal(0),
            )
            controller = MarketScopeController(
                Path(directory) / "scope.json",
                Path(directory) / "scope-events.jsonl",
            )
            trader = PaperTrader(
                report,
                settings,
                logging.getLogger("test.market-scope.drain"),
                controller,
            )
            try:
                for btc in (100, 100.05, 100.10, 100.15, 100.20, 100.35, 100.36):
                    trader.process(
                        self.xau_snapshot(Decimal(100), Decimal(str(btc))),
                        entry_enabled=True,
                    )
                self.assertEqual(trader.snapshot().positions, 1)
                controller.request(
                    MarketScope.XAU_ONLY,
                    SwitchPolicy.SWITCH_WHEN_FLAT,
                    open_positions=1,
                )
                self.assertIsNone(controller.entry_scope)
                trader.process(
                    self.xau_snapshot(Decimal(100), Decimal("100.80")),
                    entry_enabled=True,
                )
                self.assertEqual(trader.snapshot().positions, 0)
                self.assertEqual(controller.entry_scope, MarketScope.XAU_ONLY)
            finally:
                trader.close()

    def test_xau_contracts_are_retained_but_not_selected_by_crypto_screen(self) -> None:
        settings = load_settings()
        tickers = [
            {
                "contract": symbol,
                "last": "100",
                "highest_bid": "99.99",
                "lowest_ask": "100.01",
                "change_percentage": "1",
                "volume_24h_quote": "10000000",
                "funding_rate": "0",
            }
            for symbol in ("BTC_USDT", "XAU_USDT", "XAUT_USDT", "PAXG_USDT")
        ]
        symbols = ("BTC_USDT", "XAU_USDT", "XAUT_USDT", "PAXG_USDT")
        increments = {symbol: "0.01" for symbol in symbols}
        contracts = {
            symbol: {
                "price_increment": "0.01",
                "quantity_increment": "0.0001",
                "minimum_quantity": "0.0001",
                "contract_enabled": True,
                "leverage_min": "1",
                "leverage_max": "100",
            }
            for symbol in increments
        }
        ranked = rank_tickers(
            tickers,
            settings,
            ("XAU_USDT", "XAUT_USDT", "PAXG_USDT"),
            increments,
            contracts,
        )
        by_symbol = {str(item["symbol"]): item for item in ranked}
        self.assertTrue(by_symbol["BTC_USDT"]["selected"])
        self.assertFalse(by_symbol["XAU_USDT"]["selected"])
        self.assertEqual(by_symbol["XAU_USDT"]["market_class"], "xau")

    def test_analytics_separates_crypto_and_xau(self) -> None:
        trades = [
            {
                "closed_at": "2026-09-18T00:00:00Z",
                "classification": "NORMAL",
                "symbol": "BTC_USDT",
                "market_class": "crypto",
                "side": "LONG",
                "net_pnl_usdt": 1,
                "gross_price_pnl_usdt": 1.1,
                "total_fee_usdt": 0.1,
            },
            {
                "closed_at": "2026-09-18T01:00:00Z",
                "classification": "NORMAL",
                "symbol": "XAU_USDT",
                "market_class": "xau",
                "side": "SHORT",
                "regime": "TRENDING_DOWN",
                "net_pnl_usdt": -0.5,
                "gross_price_pnl_usdt": -0.4,
                "total_fee_usdt": 0.1,
            },
        ]
        result = aggregate_trades(trades, starting_equity=Decimal("300"))
        scopes = result["market_classes"]
        self.assertEqual(scopes["CRYPTO"]["trades"], 1)  # type: ignore[index]
        self.assertEqual(scopes["XAU"]["trades"], 1)  # type: ignore[index]
        self.assertEqual(scopes["XAU"]["by_direction"]["SHORT"]["trades"], 1)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
