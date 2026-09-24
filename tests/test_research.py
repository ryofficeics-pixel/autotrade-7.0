from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from autotrade.research import (
    AppendOnlyRegistry,
    DatasetEvent,
    LifecycleRegistry,
    LifecycleState,
    SignalCandidate,
    XauCombinedStrategy,
    XauFairValueStrategy,
    analyze_ama_evidence,
    analyze_entry_v3_evidence,
    counterfactual_summary,
    decompose_cost,
    entry_funnel,
    freeze_xau_dataset,
    load_dataset_events,
    load_research_settings,
    replay_strategy,
    resolve_counterfactuals,
    seal_final_holdout,
    validation_plan,
    verify_dataset,
    verify_hash_chain,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ZERO_HASH = "0" * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def source_record(
    sequence: int,
    previous_hash: str,
    event_type: str,
    payload: dict[str, object],
) -> tuple[dict[str, object], str]:
    record: dict[str, object] = {
        "schema_version": 2,
        "sequence": sequence,
        "event_type": event_type,
        "event_id": f"event-{sequence}",
        "recorded_at_utc": payload.get("observed_at_utc", "2026-01-01T00:00:00Z"),
        "run_id": "test-run",
        "session_id": "test-session",
        "previous_hash": previous_hash,
        "payload": payload,
    }
    record_hash = hashlib.sha256(previous_hash.encode() + canonical(record)).hexdigest()
    record["record_hash"] = record_hash
    return record, record_hash


def observation(index: int, timestamp: datetime, price: Decimal) -> dict[str, object]:
    return {
        "observation_id": f"observation-{index}",
        "observed_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": "XAU_USDT",
        "price": float(price),
        "spread_bps": 1.0,
        "quote_volume_usdt": 10_000_000.0,
        "kama10": float(price + Decimal(1)),
        "kama20": float(price + Decimal(2)),
        "kama50": float(price + Decimal(3)),
        "kama_slopes_bps": {"10": -0.5, "20": -0.5, "50": -0.5},
        "distance_from_kama20_bps": 25.0,
        "atr_proxy_bps": 2.0,
        "hysteresis_bps": 18.0,
        "regime": "DOWNTREND",
        "regime_observations": 5,
        "raw_direction": "SHORT",
        "confidence": 0.8,
        "reference": {
            "status": "HEALTHY",
            "reason": "WITHIN_LIMITS",
            "fair_value": float(price - Decimal(12)),
            "dispersion_bps": 2.0,
            "dislocation_bps": 30.0,
            "sources": {
                "XAU_USDT": float(price),
                "XAUT_USDT": float(price - Decimal(11)),
                "PAXG_USDT": float(price - Decimal(13)),
            },
        },
    }


def write_source(path: Path, count: int = 12) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    previous = ZERO_HASH
    rows: list[dict[str, object]] = []
    for index in range(1, count + 1):
        payload = observation(
            index,
            timestamp + timedelta(seconds=(index - 1) * 10),
            Decimal(4400) - Decimal(index - 1),
        )
        record, previous = source_record(index, previous, "observation", payload)
        rows.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))


class ResearchDatasetTests(unittest.TestCase):
    def test_freeze_hash_verify_and_mutation_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ama-control-v2.jsonl"
            config = root / "paper.toml"
            config.write_text('[trading]\nmode = "PAPER"\n', encoding="utf-8")
            write_source(source)

            manifest = freeze_xau_dataset(root, source, root / "research" / "datasets", config)
            dataset = root / "research" / "datasets" / str(manifest["dataset_id"])
            self.assertEqual(manifest["row_count"], 12)
            self.assertEqual(len(load_dataset_events(dataset)), 12)
            self.assertEqual(verify_dataset(dataset)["status"], "FROZEN")

            market = dataset / "market_events.jsonl"
            market.chmod(0o666)
            market.write_bytes(market.read_bytes() + b"{}\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_dataset(dataset)

    def test_duplicate_and_corrupt_source_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ama-control-v2.jsonl"
            config = root / "paper.toml"
            config.write_text('[trading]\nmode = "PAPER"\n', encoding="utf-8")
            write_source(source, 2)
            data = source.read_bytes().replace(b"observation-2", b"observation-1")
            source.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "record hash mismatch"):
                freeze_xau_dataset(root, source, root / "datasets", config)


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "ama-control-v2.jsonl"
        self.config = self.root / "paper.toml"
        self.config.write_text('[trading]\nmode = "PAPER"\n', encoding="utf-8")
        write_source(self.source)
        manifest = freeze_xau_dataset(
            self.root, self.source, self.root / "datasets", self.config
        )
        self.dataset = self.root / "datasets" / str(manifest["dataset_id"])
        self.events = load_dataset_events(self.dataset)
        self.settings = load_research_settings(PROJECT_ROOT / "config" / "research.toml")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_replay_is_deterministic_and_candidates_are_causal(self) -> None:
        strategy = XauFairValueStrategy({"dispersion_allowance": "1"})
        profile = self.settings.execution_profiles["BASELINE"]
        first = replay_strategy(self.events, strategy, profile)
        second = replay_strategy(self.events, strategy, profile)
        self.assertEqual(
            [candidate.record() for candidate in first],
            [candidate.record() for candidate in second],
        )
        self.assertEqual(len({candidate.candidate_id for candidate in first}), len(first))
        for candidate, event in zip(first, self.events, strict=True):
            self.assertEqual(candidate.source_event_id, event.source_event_id)
            self.assertLessEqual(candidate.timestamp, event.event_time)
            self.assertIsNone(candidate.calibrated_probability)

    def test_future_candidate_is_rejected(self) -> None:
        event = self.events[0]

        class FutureStrategy:
            strategy_id = "FUTURE"
            strategy_version = "1"
            strategy_hash = "hash"
            parameter_hash = "parameters"

            def on_market_event(self, market_state: DatasetEvent) -> SignalCandidate:
                fair = XauFairValueStrategy({"dispersion_allowance": "1"})
                candidate = fair.on_market_event(market_state)
                return SignalCandidate(
                    **{
                        **candidate.__dict__,
                        "timestamp": market_state.event_time + timedelta(seconds=1),
                    }
                )

        with self.assertRaisesRegex(ValueError, "future dataset position"):
            replay_strategy(
                [event], FutureStrategy(), self.settings.execution_profiles["BASELINE"]
            )

    def test_cost_decomposition_counterfactual_and_funnel(self) -> None:
        profile = self.settings.execution_profiles["BASELINE"]
        costs = decompose_cost(Decimal(25), Decimal(1), profile)
        self.assertEqual(costs.total_cost_bps, Decimal(18))
        self.assertEqual(costs.net_edge_bps, Decimal(7))

        strategy = XauCombinedStrategy({"maximum_spread_bps": "8"})
        candidates = replay_strategy(self.events, strategy, profile)
        outcomes = resolve_counterfactuals(
            candidates, self.events, (10, 30, 60), seed=7
        )
        self.assertLessEqual(len(outcomes), len(candidates))
        self.assertEqual(len({row["candidate_id"] for row in outcomes}), len(outcomes))
        summary = counterfactual_summary(outcomes)
        self.assertEqual(summary["total_distinct_candidates"], len(outcomes))
        funnel = entry_funnel(candidates, outcomes)
        self.assertEqual(funnel["generated"], len(candidates))
        counted = int(str(funnel["accepted"])) + int(str(funnel["rejected"]))
        self.assertEqual(counted, len(candidates))

    def test_reference_quality_blocks_fair_value_and_combined(self) -> None:
        event = self.events[0]
        stale = DatasetEvent(
            **{
                **event.__dict__,
                "reference": {"status": "UNAVAILABLE"},
            }
        )
        fair = XauFairValueStrategy({"dispersion_allowance": "1"}).on_market_event(stale)
        combined = XauCombinedStrategy({"maximum_spread_bps": "8"}).on_market_event(stale)
        self.assertIn("REFERENCE_UNAVAILABLE", fair.all_rejection_reasons)
        self.assertIn("REFERENCE_NOT_HEALTHY", combined.all_rejection_reasons)


class ValidationAndLifecycleTests(unittest.TestCase):
    def test_chronology_purge_embargo_and_holdout_seal(self) -> None:
        settings = load_research_settings(PROJECT_ROOT / "config" / "research.toml")
        start = datetime(2026, 1, 1, tzinfo=UTC)
        events = [
            DatasetEvent(
                dataset_id="dataset",
                dataset_position=index + 1,
                source_event_id=f"event-{index}",
                source_sequence=index + 1,
                event_time=start + timedelta(hours=index),
                receive_time=start + timedelta(hours=index),
                processing_time=None,
                timestamp_semantics="RECEIVE",
                symbol="XAU_USDT",
                last=Decimal(100),
                spread_bps=Decimal(1),
                quote_volume_usdt=Decimal(1),
                features={},
                reference={},
                quality_flags=(),
            )
            for index in range(20)
        ]
        plan = validation_plan(events, settings)
        stages = cast(dict[str, dict[str, object]], plan["stages"])
        self.assertFalse(plan["shuffled"])
        self.assertEqual(stages["SELECTION"]["purge_seconds"], 900)
        self.assertEqual(stages["VALIDATION"]["embargo_seconds"], 900)
        self.assertTrue(stages["FINAL_HOLDOUT"]["sealed"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            seal_final_holdout(path, "dataset", "strategy-a", "parameters-a")
            with self.assertRaisesRegex(ValueError, "different strategy identity"):
                seal_final_holdout(path, "dataset", "strategy-b", "parameters-a")

    def test_registry_restart_tamper_and_manual_paper_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = AppendOnlyRegistry(root / "registry.jsonl")
            registry.append("experiment", {"experiment_id": "one"})
            self.assertEqual(len(AppendOnlyRegistry(root / "registry.jsonl").records()), 1)
            registry.path.write_text(
                registry.path.read_text(encoding="utf-8").replace("one", "two"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "record hash mismatch"):
                verify_hash_chain(registry.path)

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = LifecycleRegistry(Path(directory))
            lifecycle.initialize(git_commit="abc")
            with self.assertRaisesRegex(ValueError, "only a VALIDATED"):
                lifecycle.operator_transition(
                    strategy_id="REST_MOMENTUM_TOURNAMENT_V2",
                    strategy_version="2",
                    target=LifecycleState.PAPER_ELIGIBLE,
                    operator="tester",
                    reason="test",
                    promotion_evidence={"promotion_eligible": True},
                )
            lifecycle.store.append(
                "validated_for_test",
                {
                    "strategy_id": "TEST_STRATEGY",
                    "strategy_version": "1",
                    "state": "VALIDATED",
                },
            )
            with self.assertRaisesRegex(ValueError, "does not permit"):
                lifecycle.operator_transition(
                    strategy_id="TEST_STRATEGY",
                    strategy_version="1",
                    target=LifecycleState.PAPER_ELIGIBLE,
                    operator="tester",
                    reason="test",
                    promotion_evidence={"promotion_eligible": False},
                )
            eligible = lifecycle.operator_transition(
                strategy_id="TEST_STRATEGY",
                strategy_version="1",
                target=LifecycleState.PAPER_ELIGIBLE,
                operator="tester",
                reason="validated evidence",
                promotion_evidence={"promotion_eligible": True},
            )
            self.assertTrue(eligible["manual_approval"])
            active = lifecycle.operator_transition(
                strategy_id="TEST_STRATEGY",
                strategy_version="1",
                target=LifecycleState.PAPER_ACTIVE,
                operator="tester",
                reason="separate activation",
            )
            self.assertFalse(active["live_permission"])


class EvidenceAnalyzerTests(unittest.TestCase):
    def test_ama_counts_distinct_candidates_not_horizons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ama.jsonl"
            timestamp = "2026-01-01T00:00:00Z"
            previous = ZERO_HASH
            rows: list[dict[str, object]] = []
            decision, previous = source_record(
                1,
                previous,
                "decision",
                {
                    "decision_id": "candidate-1",
                    "strategy_id": "XAU_KAMA20_FILTERED_V2",
                    "observed_at_utc": timestamp,
                    "candidate_direction": "LONG",
                    "regime": "UPTREND",
                    "decision": "REJECT",
                    "rejection_reason": "HYSTERESIS_BAND",
                },
            )
            rows.append(decision)
            for sequence, horizon in enumerate((10, 30), 2):
                outcome, previous = source_record(
                    sequence,
                    previous,
                    "counterfactual_outcome",
                    {
                        "decision_id": "candidate-1",
                        "strategy_id": "XAU_KAMA20_FILTERED_V2",
                        "horizon_seconds": horizon,
                        "modeled_net_return_bps": -5,
                        "mfe_bps": 1,
                        "mae_bps": -6,
                    },
                )
                rows.append(outcome)
            path.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
            result = analyze_ama_evidence(path)
            self.assertEqual(result["distinct_candidate_count"], 1)
            self.assertEqual(result["resolved_candidate_count"], 1)
            summary = cast(dict[str, object], result["counterfactual"])
            self.assertEqual(summary["total_distinct_candidates"], 1)

    def test_entry_v3_funnel_finds_binding_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entry-v3-events.jsonl"
            rows = [
                {
                    "event_type": "entry_candidate",
                    "candidate_id": f"candidate-{index}",
                    "decision_timestamp": 1_000 + index * 1_000,
                    "direction": "LONG",
                    "decision": "REJECTED",
                    "rejection_reason": "CHOP_REGIME" if index < 3 else "CHASE_TOO_EXTENDED",
                }
                for index in range(4)
            ]
            path.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
            result = analyze_entry_v3_evidence(path)
            funnel = cast(dict[str, object], result["funnel"])
            binding = cast(list[dict[str, object]], funnel["binding_gates"])
            self.assertEqual(binding[0], {"gate": "REGIME", "reject_count": 3})

    def test_entry_v3_aggregates_capture_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths: list[Path] = []
            for capture, reason in enumerate(("CHOP_REGIME", "CHASE_TOO_EXTENDED")):
                path = Path(directory) / f"capture-{capture}.jsonl"
                rows = [
                    {
                        "event_type": "entry_candidate",
                        "candidate_id": f"candidate-{capture}-{index}",
                        "decision_timestamp": capture * 10_000 + index * 1_000,
                        "direction": "LONG",
                        "decision": "REJECTED",
                        "rejection_reason": reason,
                    }
                    for index in range(2)
                ]
                path.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
                paths.append(path)
            result = analyze_entry_v3_evidence(paths)
            funnel = cast(dict[str, object], result["funnel"])
            source = cast(dict[str, object], result["source"])
            counterfactual = cast(dict[str, object], result["counterfactual"])
            self.assertEqual(result["distinct_candidate_count"], 4)
            self.assertEqual(funnel["generated"], 4)
            self.assertEqual(source["capture_count"], 2)
            self.assertEqual(counterfactual["time_in_no_trade_seconds"], 2.0)


if __name__ == "__main__":
    unittest.main()
