from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from autotrade.ama_control import (
    AmaControlEngine,
    AmaRegime,
    EntryQualityGate,
    QualityDecision,
    XauReferenceEngine,
)
from autotrade.config import ConfigError, load_settings


def market(symbol: str, price: Decimal, spread_bps: Decimal = Decimal("0.2")) -> dict[str, object]:
    return {
        "symbol": symbol,
        "last": float(price),
        "spread_bps": float(spread_bps),
        "volume_quote": 10_000_000,
    }


class AmaControlTests(unittest.TestCase):
    def settings(self):  # type: ignore[no-untyped-def]
        return replace(
            load_settings().ama_control,
            fast_period=2,
            control_period=3,
            slow_period=5,
            atr_proxy_window=2,
            atr_multiplier=Decimal("0.1"),
            minimum_hysteresis_bps=Decimal("0.1"),
            minimum_slope_bps=Decimal("0.001"),
            maximum_distance_bps=Decimal("500"),
            minimum_net_edge_bps=Decimal("0.1"),
            round_trip_fee_bps=Decimal("0.1"),
            slippage_bps_per_side=Decimal(0),
            funding_buffer_bps=Decimal(0),
            maximum_holding_seconds=12,
            minimum_comparison_trades=2,
            quarantine_minimum_trades=2,
            report_interval_seconds=10_000,
        )

    def test_kama_control_is_shadow_only_and_writes_hash_chained_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AmaControlEngine(
                self.settings(), project_root=root, run_id="run-test", session_id="session-test"
            )
            prices = [Decimal(value) for value in (100, 101, 102, 103, 104, 105, 104, 103, 102)]
            for index, price in enumerate(prices):
                engine.update(
                    {
                        "XAU_USDT": market("XAU_USDT", price),
                        "XAUT_USDT": market("XAUT_USDT", price - Decimal("0.02")),
                        "PAXG_USDT": market("PAXG_USDT", price + Decimal("0.02")),
                    },
                    observed_at=float(index * 5),
                    observed_at_utc=f"2026-09-19T00:00:{index * 5:02d}Z",
                )

            snapshot = engine.snapshot()
            self.assertFalse(snapshot["execution_enabled"])
            self.assertEqual(snapshot["execution_influence"], "NONE")
            self.assertNotEqual(snapshot["regime"], AmaRegime.UNKNOWN.value)
            self.assertEqual(snapshot["evidence_integrity"], "VALID")
            evidence = root / "data" / "runs" / "run-test" / "ama-control-v2.jsonl"
            records = [
                json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any(record["event_type"] == "decision" for record in records))
            self.assertTrue(
                any(record["event_type"] == "counterfactual_outcome" for record in records)
            )
            outcomes = [
                record["payload"]
                for record in records
                if record["event_type"] == "counterfactual_outcome"
            ]
            self.assertTrue(
                all("mfe_bps" in outcome and "mae_bps" in outcome for outcome in outcomes)
            )
            self.assertTrue(all(float(outcome["modeled_cost_bps"]) >= 0.3 for outcome in outcomes))
            strategies = cast(dict[str, dict[str, object]], snapshot["strategies"])
            raw_metrics = strategies["XAU_KAMA20_RAW_V2"]
            self.assertTrue(raw_metrics["by_direction"])
            self.assertIn("execution_profiles", raw_metrics)
            self.assertTrue(
                all(
                    record["payload"].get("execution", {}).get("enabled") is not True
                    for record in records
                )
            )
            for previous, current in zip(records, records[1:], strict=False):
                self.assertEqual(current["previous_hash"], previous["record_hash"])

    def test_existing_evidence_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AmaControlEngine(
                self.settings(), project_root=root, run_id="run-test", session_id="one"
            )
            inputs = {
                "XAU_USDT": market("XAU_USDT", Decimal(100)),
                "XAUT_USDT": market("XAUT_USDT", Decimal(100)),
                "PAXG_USDT": market("PAXG_USDT", Decimal(100)),
            }
            engine.update(inputs, observed_at=0, observed_at_utc="2026-09-19T00:00:00Z")
            path = root / "data" / "runs" / "run-test" / "ama-control-v2.jsonl"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    '"record_hash":"', '"record_hash":"deadbeef', 1
                ),
                encoding="utf-8",
            )
            restarted = AmaControlEngine(
                self.settings(), project_root=root, run_id="run-test", session_id="two"
            )
            self.assertEqual(restarted.snapshot()["evidence_integrity"], "INVALID")

    def test_restart_restores_kama_shadow_position_and_pending_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings()
            engine = AmaControlEngine(
                settings, project_root=root, run_id="run-test", session_id="one"
            )
            started = datetime.now(UTC) - timedelta(seconds=25)
            for index, value in enumerate((100, 101, 102, 103, 104, 105)):
                timestamp = (
                    (started + timedelta(seconds=index * 5)).isoformat().replace("+00:00", "Z")
                )
                price_value = Decimal(value)
                engine.update(
                    {
                        "XAU_USDT": market("XAU_USDT", price_value),
                        "XAUT_USDT": market("XAUT_USDT", price_value),
                        "PAXG_USDT": market("PAXG_USDT", price_value),
                    },
                    observed_at=float(index * 5),
                    observed_at_utc=timestamp,
                )
            restarted = AmaControlEngine(
                settings, project_root=root, run_id="run-test", session_id="two"
            ).snapshot()
            strategies = cast(dict[str, dict[str, object]], restarted["strategies"])
            raw = strategies["XAU_KAMA20_RAW_V2"]
            self.assertEqual(raw["open_position"], "LONG")
            self.assertEqual(restarted["observations"], 6)
            self.assertGreater(int(str(restarted["counterfactual_pending"])), 0)

    def test_quality_gate_uses_deterministic_rejection_taxonomy(self) -> None:
        gate = EntryQualityGate(self.settings())
        decision = gate.decide(
            ready=True,
            direction="LONG",
            regime=AmaRegime.UPTREND,
            distance_bps=Decimal(10),
            slope_bps=Decimal(1),
            hysteresis_bps=Decimal(1),
            net_edge_bps=Decimal(9),
            confidence=Decimal("0.9"),
            spread_bps=Decimal(99),
            quote_volume_usdt=Decimal(10_000_000),
            reference_status="HEALTHY",
            stale=False,
            quarantined=False,
            regime_persistent=True,
            risk_valid=True,
        )
        self.assertEqual(decision, QualityDecision.SPREAD_LIMIT)

    def test_quality_gate_rejects_flat_slope_and_extended_price(self) -> None:
        gate = EntryQualityGate(self.settings())
        self.assertEqual(
            gate.decide(
                ready=True,
                direction="LONG",
                regime=AmaRegime.UPTREND,
                distance_bps=Decimal(10),
                slope_bps=Decimal("0.0001"),
                hysteresis_bps=Decimal(1),
                net_edge_bps=Decimal(20),
                confidence=Decimal("0.9"),
                spread_bps=Decimal("0.2"),
                quote_volume_usdt=Decimal(10_000_000),
                reference_status="HEALTHY",
                stale=False,
                quarantined=False,
                regime_persistent=True,
                risk_valid=True,
            ),
            QualityDecision.SLOPE_TOO_FLAT,
        )
        self.assertEqual(
            gate.decide(
                ready=True,
                direction="LONG",
                regime=AmaRegime.UPTREND,
                distance_bps=Decimal(501),
                slope_bps=Decimal(1),
                hysteresis_bps=Decimal(1),
                net_edge_bps=Decimal(20),
                confidence=Decimal("0.9"),
                spread_bps=Decimal("0.2"),
                quote_volume_usdt=Decimal(10_000_000),
                reference_status="HEALTHY",
                stale=False,
                quarantined=False,
                regime_persistent=True,
                risk_valid=True,
            ),
            QualityDecision.EXTENDED_PRICE,
        )

    def test_reports_cover_required_markdown_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = AmaControlEngine(
                self.settings(), project_root=root, run_id="run-test", session_id="session-test"
            )
            engine.write_reports()
            expected = {
                "strategy_health_latest.md",
                "ama20_control_latest.md",
                "complexity_audit_latest.md",
                "xau_research_latest.md",
                "entry_quality_latest.md",
                "counterfactual_latest.md",
                "execution_realism_latest.md",
                "walk_forward_latest.md",
            }
            self.assertTrue(expected.issubset({path.name for path in (root / "reports").iterdir()}))
            ama_report = (root / "reports" / "ama20_control_latest.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Is the current main strategy outperforming AMA?", ama_report)
            self.assertIn("INSUFFICIENT EVIDENCE", ama_report)

    def test_reference_engine_rejects_abnormal_dislocation(self) -> None:
        reference = XauReferenceEngine(self.settings()).evaluate(
            {
                "XAU_USDT": market("XAU_USDT", Decimal(110)),
                "XAUT_USDT": market("XAUT_USDT", Decimal(100)),
                "PAXG_USDT": market("PAXG_USDT", Decimal(100)),
            }
        )
        self.assertEqual(reference["status"], "ABNORMAL")

    def test_config_rejects_ama_execution(self) -> None:
        original = Path("config/paper.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.toml"
            path.write_text(
                original.replace(
                    "[xau.ama_control]\nenabled = true\nexecution_enabled = false",
                    "[xau.ama_control]\nenabled = true\nexecution_enabled = true",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "execution_enabled must remain false"):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
