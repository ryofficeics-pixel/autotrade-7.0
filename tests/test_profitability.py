from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from autotrade.analytics import (
    aggregate_trades,
    chronological_split,
    load_jsonl,
    reconstruct_trades,
)
from autotrade.experiments import (
    PaperProtectiveOrderBook,
    ProtectiveOrder,
    capped_notional_quantity,
    churn_block_reason,
    cost_gate_reason,
    deterministic_candidate_ranking,
    market_quality_reasons,
    partial_runner_quantities,
    risk_adjusted_quantity,
)
from autotrade.outage_analysis import analyze_long_outage
from autotrade.time_utils import canonical_utc, duration_ms, utc_to_wib

ROOT = Path(__file__).resolve().parents[1]
INCIDENT_LEDGER = ROOT / "data" / "runs" / "aa89e54a-e88a-4732-b254-615a58f1721a" / "events.jsonl"


class TimestampTests(unittest.TestCase):
    def test_utc_wib_conversion_crosses_date_boundary(self) -> None:
        value = "2026-09-14T18:30:00Z"
        self.assertEqual(canonical_utc(value), "2026-09-14T18:30:00.000000Z")
        self.assertEqual(utc_to_wib(value), "2026-09-15T01:30:00.000000+07:00")
        self.assertEqual(duration_ms(value, "2026-09-14T18:30:01.250000Z"), 1250)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            canonical_utc("2026-09-15T12:00:00")


class ExperimentPrimitiveTests(unittest.TestCase):
    def test_risk_sizing_equalizes_different_stop_distances(self) -> None:
        common = {
            "entry_price": Decimal("100"),
            "risk_budget_usdt": Decimal("1"),
            "fee_buffer_bps": Decimal("10"),
            "slippage_buffer_bps": Decimal("4"),
            "maximum_position_notional_usdt": Decimal("1000"),
            "maximum_symbol_exposure_usdt": Decimal("1000"),
            "liquidity_cap_usdt": Decimal("1000"),
            "size_increment": Decimal("0.000001"),
            "minimum_quantity": Decimal("0.000001"),
        }
        tight = risk_adjusted_quantity(stop_distance_bps=Decimal("35"), **common)
        wide = risk_adjusted_quantity(stop_distance_bps=Decimal("70"), **common)
        self.assertIsNone(tight.reason)
        self.assertIsNone(wide.reason)
        self.assertAlmostEqual(float(tight.intended_risk_usdt), 1.0, places=5)
        self.assertAlmostEqual(float(wide.intended_risk_usdt), 1.0, places=5)
        self.assertGreater(tight.quantity, wide.quantity)

    def test_dynamic_notional_never_exceeds_cap(self) -> None:
        low = capped_notional_quantity(
            entry_price=Decimal("0.06856212066666711"),
            notional_cap_usdt=Decimal("30"),
            size_increment=Decimal("0.000001"),
        )
        high = capped_notional_quantity(
            entry_price=Decimal("0.07098787933333289"),
            notional_cap_usdt=Decimal("30"),
            size_increment=Decimal("0.000001"),
        )
        self.assertLessEqual(low.entry_notional_usdt, Decimal("30"))
        self.assertLessEqual(high.entry_notional_usdt, Decimal("30"))
        self.assertNotEqual(low.quantity, high.quantity)

    def test_partial_runner_quantity_is_conserved(self) -> None:
        for fraction in (Decimal("0.75"), Decimal("0.50")):
            initial, runner = partial_runner_quantities(
                Decimal("48.520135"), fraction, Decimal("0.000001")
            )
            self.assertEqual(initial + runner, Decimal("48.520135"))
            self.assertGreater(runner, 0)

    def test_protective_order_book_is_idempotent_and_fail_closed(self) -> None:
        book = PaperProtectiveOrderBook()
        stop = ProtectiveOrder(
            "paper-stop-1",
            "position-1",
            "STOP",
            Decimal("1"),
            Decimal("99"),
        )
        self.assertEqual(book.place(stop), book.place(stop))
        self.assertEqual(book.reconcile("position-1", Decimal("1")), (stop,))
        self.assertEqual(book.cancel("paper-stop-1"), book.cancel("paper-stop-1"))
        with self.assertRaisesRegex(RuntimeError, "MISSING_PROTECTIVE_STOP"):
            book.reconcile("position-1", Decimal("1"))

    def test_candidate_ranking_is_deterministic(self) -> None:
        candidates = [
            {"symbol": "Z_USDT", "confidence": 0.8, "expected_net_bps": 20, "spread_bps": 4},
            {"symbol": "A_USDT", "confidence": 0.8, "expected_net_bps": 20, "spread_bps": 3},
            {"symbol": "B_USDT", "confidence": 0.9, "expected_net_bps": 15, "spread_bps": 8},
        ]
        expected = ["B_USDT", "A_USDT", "Z_USDT"]
        self.assertEqual(
            [item["symbol"] for item in deterministic_candidate_ranking(candidates)],
            expected,
        )
        self.assertEqual(
            [item["symbol"] for item in deterministic_candidate_ranking(reversed(candidates))],
            expected,
        )

    def test_cost_and_stale_market_gates_have_auditable_codes(self) -> None:
        self.assertEqual(
            cost_gate_reason(Decimal("20"), Decimal("15"), Decimal("1.5")),
            "COST_TO_EDGE_REJECTED",
        )
        reasons = market_quality_reasons(
            {"spread_bps": 4, "volume_quote": 10_000_000, "age_seconds": 16},
            maximum_spread_bps=Decimal("8"),
            minimum_quote_volume=Decimal("5000000"),
            minimum_depth_usdt=Decimal(0),
            maximum_one_bar_volatility_bps=Decimal(0),
            maximum_price_gap_bps=Decimal(0),
            require_order_book=False,
            stale_after_seconds=Decimal("15"),
        )
        self.assertEqual(reasons, ["STALE_MARKET_DATA"])

    def test_churn_guards_cover_reset_cooldown_attempts_and_loss(self) -> None:
        now = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)
        history = [
            {
                "symbol": "CAP_USDT",
                "classification": "NORMAL",
                "closed_at": "2026-09-15T05:50:00Z",
                "net_pnl_usdt": -0.6,
            },
            {
                "symbol": "CAP_USDT",
                "classification": "NORMAL",
                "closed_at": "2026-09-15T05:55:00Z",
                "net_pnl_usdt": -0.6,
            },
        ]
        self.assertEqual(
            churn_block_reason(
                history,
                symbol="CAP_USDT",
                now_utc=now,
                signal_reset_required=True,
                signal_has_reset=False,
                maximum_attempts=2,
                attempt_window_seconds=3600,
                maximum_consecutive_losses=2,
                maximum_utc_day_loss_usdt=Decimal("1"),
                maximum_wib_day_loss_usdt=Decimal("1"),
            ),
            "SIGNAL_RESET_REQUIRED",
        )
        self.assertEqual(
            churn_block_reason(
                history,
                symbol="CAP_USDT",
                now_utc=now,
                signal_reset_required=True,
                signal_has_reset=True,
                maximum_attempts=2,
                attempt_window_seconds=3600,
                maximum_consecutive_losses=2,
                maximum_utc_day_loss_usdt=Decimal("1"),
                maximum_wib_day_loss_usdt=Decimal("1"),
            ),
            "SYMBOL_ATTEMPT_LIMIT",
        )

    def test_walk_forward_split_has_no_overlap_or_lookahead(self) -> None:
        split = chronological_split(35)
        selection = set(split.selection)
        validation = set(split.validation)
        holdout = set(split.holdout)
        self.assertFalse(selection & validation)
        self.assertFalse(selection & holdout)
        self.assertFalse(validation & holdout)
        self.assertLess(max(selection), min(validation))
        self.assertLess(max(validation), min(holdout))
        self.assertEqual(selection | validation | holdout, set(range(35)))

    def test_outage_counterfactual_orders_distinct_candle_triggers(self) -> None:
        result = analyze_long_outage(
            [
                {"t": 1789447140, "h": "101", "l": "100", "c": "100.5"},
                {"t": 1789447200, "h": "100", "l": "98", "c": "99"},
            ],
            entry_utc="2026-09-15T04:38:00Z",
            entry_price=Decimal("100"),
            quantity=Decimal("1"),
            entry_fee_usdt=Decimal("0.05"),
            stop_loss_bps=Decimal("35"),
            take_profit_bps=Decimal("55"),
            slippage_bps=Decimal("2"),
            price_increment=Decimal("0.01"),
            recovery_pnl_usdt=Decimal("2"),
        )
        self.assertEqual(result["trigger_order"], "TAKE_PROFIT_FIRST")
        self.assertEqual(result["first_take_profit_utc"], "2026-09-15T04:39:00.000000Z")
        self.assertEqual(result["first_stop_utc"], "2026-09-15T04:40:00.000000Z")


class IncidentRegressionTests(unittest.TestCase):
    def test_incident_baseline_and_manual_classification(self) -> None:
        events = [
            event
            for event in load_jsonl(INCIDENT_LEDGER)
            if int(str(event.get("sequence", 0))) <= 2385
        ]
        trades = [
            trade
            for trade in reconstruct_trades(events)
            if str(trade["closed_at"]).startswith("2026-09-15")
        ]
        position_closes = [trade for trade in trades if trade["classification"] != "OUTAGE_HELD"]
        normal = [trade for trade in trades if trade["classification"] == "NORMAL"]
        manual = [trade for trade in trades if trade["classification"] == "MANUAL"]
        self.assertEqual(len(position_closes), 35)
        self.assertEqual(len(normal), 34)
        self.assertEqual(len(manual), 1)
        summary = aggregate_trades(position_closes, starting_equity=Decimal("300"))["full_run"]
        assert isinstance(summary, dict)
        self.assertAlmostEqual(float(str(summary["net_pnl_usdt"])), -2.08830097, places=8)
        self.assertEqual(summary["wins"], 11)
        self.assertEqual(summary["losses"], 24)

    def test_cap_outliers_are_gap_overshoots_not_fee_or_rounding_losses(self) -> None:
        events = [
            event
            for event in load_jsonl(INCIDENT_LEDGER)
            if int(str(event.get("sequence", 0))) <= 2385
        ]
        trades = reconstruct_trades(events)
        target = {
            "2812b14f-adec-4061-bc67-090268e81779",
            "9effd163-fb06-4c2e-b415-edc6ecbd8c2b",
        }
        outliers = [trade for trade in trades if trade["position_id"] in target]
        self.assertEqual(len(outliers), 2)
        for trade in outliers:
            self.assertGreater(float(str(trade["entry_notional_usdt"])), 30)
            self.assertLess(float(str(trade["net_pnl_usdt"])), -0.61)
            self.assertGreater(
                abs(float(str(trade["gross_price_pnl_usdt"]))),
                float(str(trade["total_fee_usdt"])) * 15,
            )


if __name__ == "__main__":
    unittest.main()
