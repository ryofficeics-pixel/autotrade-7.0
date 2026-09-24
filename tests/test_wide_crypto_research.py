from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

from autotrade.research import ExecutionProfile
from autotrade.robust_validation import cpcv, parameter_stability, promotion_gate
from autotrade.wide_crypto_data import (
    Candle,
    select_snapshot_universe,
    verify_wide_crypto_dataset,
)
from autotrade.wide_crypto_strategy import (
    InstrumentFeatures,
    SimulatedTrade,
    StrategyFamily,
    WideStrategySettings,
    average_ranks,
    btc_regime_at,
    build_candidates,
    cross_sectional_zscores,
    features_at,
    prior_donchian,
    replay_family,
    roundtrip_cost_bps,
)


def settings() -> WideStrategySettings:
    return WideStrategySettings(
        momentum_hours=(2, 4, 8),
        momentum_weights=(Decimal("0.45"), Decimal("0.35"), Decimal("0.20")),
        tail_fraction=Decimal("0.25"),
        donchian_hours=3,
        atr_hours=3,
        volatility_baseline_hours=8,
        volume_baseline_hours=4,
        minimum_volume_ratio=Decimal("0.5"),
        minimum_volatility_ratio=Decimal("0.5"),
        maximum_breakout_extension_atr=Decimal("5"),
        edge_cost_ratio=Decimal("1"),
        stop_atr=Decimal("1.3"),
        profit_atr=Decimal("2.7"),
        trail_activation_r=Decimal("1.5"),
        trail_atr=Decimal("1.5"),
        time_stop_hours=6,
        btc_ema_fast_4h=2,
        btc_ema_slow_4h=4,
        btc_slope_periods_4h=1,
        minimum_universe_size=3,
        minimum_rolling_24h_quote_volume_usdt=Decimal("1"),
    )


def profile(name: str = "BASELINE") -> ExecutionProfile:
    return ExecutionProfile(
        profile_id=name,
        version="1",
        fee_bps=Decimal("10"),
        spread_multiplier=Decimal("1"),
        slippage_bps=Decimal("4"),
        latency_bps=Decimal("2"),
        adverse_selection_bps=Decimal("1"),
        funding_bps=Decimal("3"),
        impact_bps=Decimal("0"),
        fill_probability=Decimal("1"),
        partial_fill_ratio=Decimal("1"),
    )


def candles(symbol: str, multiplier: Decimal, count: int = 100) -> list[Candle]:
    result: list[Candle] = []
    base = Decimal("100")
    for index in range(count):
        close = base + multiplier * Decimal(index) + Decimal(index % 5) / Decimal(10)
        result.append(
            Candle(
                timestamp=1_700_000_000 + index * 3600,
                symbol=symbol,
                interval="1h",
                open=close - Decimal("0.2"),
                high=close + Decimal("0.6"),
                low=close - Decimal("0.6"),
                close=close,
                volume_contracts=Decimal(1000 + index),
                quote_volume_usdt=Decimal(10_000_000 + index * 1000),
            )
        )
    return result


def four_hour(source: list[Candle]) -> list[Candle]:
    aligned = [replace(row, timestamp=row.timestamp - row.timestamp % 14_400) for row in source]
    unique: dict[int, Candle] = {}
    for row in aligned:
        unique[row.timestamp] = row
    start = min(unique)
    synthetic: list[Candle] = []
    for index in range(24):
        timestamp = start + index * 14_400
        close = Decimal(100 + index)
        synthetic.append(
            Candle(
                timestamp=timestamp,
                symbol="BTC_USDT",
                interval="4h",
                open=close - 1,
                high=close + 1,
                low=close - 1,
                close=close,
                volume_contracts=Decimal(1),
                quote_volume_usdt=Decimal(1),
            )
        )
    return synthetic


class UniverseAndDatasetTests(unittest.TestCase):
    def test_universe_ranking_and_rejection_taxonomy(self) -> None:
        now = 1_800_000_000
        contracts = [
            {"name": name, "create_time": now - 400 * 86400, "in_delisting": False}
            for name in ("BTC_USDT", "ETH_USDT", "USDC_USDT", "XAG_USDT", "NEW_USDT")
        ]
        contracts[-1]["create_time"] = now - 2 * 86400
        tickers = [
            {
                "contract": name,
                "volume_24h_quote": volume,
                "highest_bid": "99",
                "lowest_ask": "100",
            }
            for name, volume in (
                ("BTC_USDT", "100000000"),
                ("ETH_USDT", "50000000"),
                ("USDC_USDT", "90000000"),
                ("XAG_USDT", "85000000"),
                ("NEW_USDT", "80000000"),
            )
        ]
        selected, rejected = select_snapshot_universe(
            tickers,
            contracts,
            now_timestamp=now,
            universe_size=2,
            minimum_listing_days=180,
            minimum_quote_volume=Decimal("1000000"),
            maximum_spread_bps=Decimal("200"),
        )
        self.assertEqual([row["symbol"] for row in selected], ["BTC_USDT", "ETH_USDT"])
        reasons = {
            str(row["symbol"]): cast(list[str], row["rejection_reasons"]) for row in rejected
        }
        self.assertIn("UNSUPPORTED_MARKET_CLASS", reasons["USDC_USDT"])
        self.assertIn("UNSUPPORTED_MARKET_CLASS", reasons["XAG_USDT"])
        self.assertIn("NEW_LISTING", reasons["NEW_USDT"])

    def test_dataset_verification_detects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "wide-crypto-v1-test"
            root.mkdir()
            candle = root / "candles_1h.jsonl"
            candle.write_text('{"row":1}\n', encoding="utf-8")
            universe = root / "universe.json"
            universe.write_text("{}\n", encoding="utf-8")
            files = {
                path.name: {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                }
                for path in (candle, universe)
            }
            manifest = {
                "dataset_id": root.name,
                "status": "FROZEN",
                "immutable": True,
                "files": files,
            }
            encoded = (
                json.dumps(
                    manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                + b"\n"
            )
            (root / "manifest.json").write_bytes(encoded)
            (root / "manifest.sha256").write_text(
                hashlib.sha256(encoded).hexdigest(), encoding="ascii"
            )
            self.assertEqual(verify_wide_crypto_dataset(root)["status"], "FROZEN")
            candle.write_text('{"row":2}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_wide_crypto_dataset(root)


class SignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.by_symbol = {
            "BTC_USDT": candles("BTC_USDT", Decimal("0.20")),
            "ETH_USDT": candles("ETH_USDT", Decimal("0.30")),
            "SOL_USDT": candles("SOL_USDT", Decimal("0.40")),
            "XRP_USDT": candles("XRP_USDT", Decimal("-0.05")),
        }
        self.settings = settings()

    def test_zscores_ties_and_prior_only_donchian(self) -> None:
        zscores = cross_sectional_zscores({"A": Decimal(1), "B": Decimal(2), "C": Decimal(3)})
        self.assertAlmostEqual(float(sum(zscores.values())), 0.0)
        ranks = average_ranks({"A": Decimal(2), "B": Decimal(2), "C": Decimal(1)})
        self.assertEqual(ranks["A"], ranks["B"])
        source = self.by_symbol["BTC_USDT"]
        high, _ = prior_donchian(source, 10, 3)
        mutated = list(source)
        mutated[10] = replace(mutated[10], high=Decimal("99999"))
        self.assertEqual(prior_donchian(mutated, 10, 3)[0], high)

    def test_features_are_causal(self) -> None:
        timestamp = self.by_symbol["BTC_USDT"][50].timestamp
        original, _ = features_at(self.by_symbol, timestamp, self.settings)
        changed = {symbol: list(rows) for symbol, rows in self.by_symbol.items()}
        changed["ETH_USDT"][80] = replace(changed["ETH_USDT"][80], close=Decimal("99999"))
        after, _ = features_at(changed, timestamp, self.settings)
        self.assertEqual(original, after)

    def test_btc_regime_cost_and_movement_gate(self) -> None:
        regime = btc_regime_at(four_hour(self.by_symbol["BTC_USDT"]), 1_800_000_000, self.settings)
        self.assertEqual(regime, "BULL")
        self.assertEqual(roundtrip_cost_bps(profile(), Decimal("2")), Decimal("22"))
        feature = InstrumentFeatures(
            timestamp=1,
            symbol="BTC_USDT",
            close=Decimal(100),
            momentum_12h=Decimal(10),
            momentum_24h=Decimal(20),
            momentum_72h=Decimal(30),
            normalized_momentum=Decimal(2),
            rank=Decimal(1),
            percentile=Decimal(1),
            realized_volatility_bps=Decimal(50),
            atr=Decimal(1),
            atr_bps=Decimal(100),
            volatility_ratio=Decimal(2),
            volume_ratio=Decimal(2),
            rolling_quote_volume_usdt=Decimal(1_000_000),
            prior_donchian_high=Decimal(99),
            prior_donchian_low=Decimal(90),
            long_breakout_bps=Decimal(50),
            short_breakout_bps=Decimal(-50),
            liquidity_regime="HIGH",
            volatility_regime="EXPANDING",
        )
        candidate = build_candidates(
            StrategyFamily.CROSS_SECTIONAL_BREAKOUT_V1,
            {
                "BTC_USDT": feature,
                "ETH_USDT": replace(feature, symbol="ETH_USDT", rank=Decimal(2)),
                "SOL_USDT": replace(feature, symbol="SOL_USDT", rank=Decimal(3)),
            },
            dataset_id="dataset",
            config_hash="config",
            parameter_version="test",
            btc_regime="BULL",
            profile=profile(),
            snapshot_spreads={
                "BTC_USDT": Decimal(2),
                "ETH_USDT": Decimal(2),
                "SOL_USDT": Decimal(2),
            },
            settings=replace(self.settings, edge_cost_ratio=Decimal(3)),
        )[0]
        self.assertEqual(candidate.decision, "ACCEPTED")
        self.assertGreaterEqual(candidate.expected_move_bps, candidate.movement_budget_bps)

    def test_replay_is_deterministic_and_holds_one_position(self) -> None:
        btc_four_hour = four_hour(self.by_symbol["BTC_USDT"])
        first = replay_family(
            StrategyFamily.CS_MOMENTUM_ONLY_V1,
            self.by_symbol,
            btc_four_hour,
            dataset_id="dataset",
            config_hash="config",
            parameter_version="test",
            profile=profile(),
            snapshot_spreads={symbol: Decimal(1) for symbol in self.by_symbol},
            settings=self.settings,
        )
        second = replay_family(
            StrategyFamily.CS_MOMENTUM_ONLY_V1,
            self.by_symbol,
            btc_four_hour,
            dataset_id="dataset",
            config_hash="config",
            parameter_version="test",
            profile=profile(),
            snapshot_spreads={symbol: Decimal(1) for symbol in self.by_symbol},
            settings=self.settings,
        )
        self.assertEqual(
            [trade.record() for trade in first[1]], [trade.record() for trade in second[1]]
        )
        self.assertTrue(
            all(
                left.exit_timestamp < right.entry_timestamp
                for left, right in zip(first[1], first[1][1:], strict=False)
            )
        )


class ValidationTests(unittest.TestCase):
    def test_cpcv_stability_and_fail_closed_promotion(self) -> None:
        timestamps = [1_700_000_000 + index * 3600 for index in range(80)]
        trades = [
            SimulatedTrade(
                trade_id=f"trade-{index}",
                candidate_id=f"candidate-{index}",
                strategy_id="TEST",
                symbol="BTC_USDT",
                direction="LONG",
                entry_timestamp=timestamp,
                exit_timestamp=timestamp + 1800,
                decision_price=Decimal(100),
                simulated_fill_price=Decimal(100),
                exit_price=Decimal(101),
                exit_reason="TIME_STOP",
                holding_hours=1,
                btc_regime="BULL",
                volatility_regime="EXPANDING",
                liquidity_regime="HIGH",
                momentum_percentile=Decimal(1),
                breakout_strength_bps=Decimal(10),
                expected_move_bps=Decimal(50),
                execution_cost_bps=Decimal(20),
                edge_cost_ratio=Decimal("2.5"),
                gross_pnl_bps=Decimal(100),
                net_pnl_bps=Decimal(80),
                mfe_bps=Decimal(100),
                mae_bps=Decimal(-10),
                mfe_capture=Decimal(1),
                latency_model="ONE_BAR_ENTRY_LAG",
                cost_model="TEST",
            )
            for index, timestamp in enumerate(timestamps[::4])
        ]
        result = cpcv(
            trades,
            timestamps,
            groups=8,
            test_groups=2,
            purge_hours=0,
            embargo_hours=0,
        )
        self.assertEqual(result["number_of_paths"], 28)
        self.assertEqual(result["positive_path_fraction"], 1.0)
        stability = parameter_stability(
            {
                "2.5": {
                    "sample_size": 10,
                    "net_expectancy_bps": 1,
                    "profit_factor": 2,
                    "net_pnl_bps": 10,
                },
                "3.0": {
                    "sample_size": 9,
                    "net_expectancy_bps": 1,
                    "profit_factor": 2,
                    "net_pnl_bps": 9,
                },
            }
        )
        self.assertEqual(stability["status"], "STABLE")
        metrics = {
            "sample_size": 20,
            "gross_expectancy_bps": 10,
            "net_expectancy_bps": 5,
            "profit_factor": 1.5,
        }
        stage = {"sample_size": 5, "net_expectancy_bps": 1}
        promotion = promotion_gate(
            metrics,
            {"VALIDATION": stage, "TEST": stage, "FINAL_HOLDOUT": stage},
            metrics,
            stability,
            minimum_trades=100,
            minimum_profit_factor=1.1,
        )
        self.assertFalse(promotion["promotion_eligible"])
        reasons = cast(list[str], promotion["reasons"])
        self.assertIn("INSUFFICIENT_INDEPENDENT_SAMPLE", reasons)
        self.assertIn("EXECUTION_MODEL_NOT_VALIDATED", reasons)


if __name__ == "__main__":
    unittest.main()
