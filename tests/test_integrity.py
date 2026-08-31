from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from autotrade.integrity import (
    EVENT_FIELDS,
    EventLedger,
    IntegrityError,
    create_new_run,
    new_identity,
    reconcile_checkpoint,
    scan_ledger,
    utc_now,
)
from autotrade.paper import QuoteValidationError, construct_pessimistic_prices


class IntegrityTests(unittest.TestCase):
    def test_v3_event_is_complete_and_malformed_event_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_id = new_identity()
            ledger = EventLedger(Path(directory) / "events.jsonl", run_id, new_identity())
            signal_id = new_identity()
            event = ledger.append(
                "signal",
                symbol="ETH_USDT",
                strategy_id="TEST",
                signal_id=signal_id,
                side="LONG",
                decision_timestamp=utc_now(),
            )
            self.assertEqual(set(EVENT_FIELDS) - set(event), set())
            self.assertEqual(event["sequence"], 1)
            self.assertEqual(event["run_id"], run_id)
            with self.assertRaisesRegex(IntegrityError, "missing required values"):
                ledger.append(
                    "signal",
                    symbol="ETH_USDT",
                    strategy_id="TEST",
                    signal_id=new_identity(),
                    decision_timestamp=utc_now(),
                )

    def test_duplicate_event_id_and_sequence_regression_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            run_id = new_identity()
            ledger = EventLedger(path, run_id, new_identity())
            first = ledger.append(
                "signal",
                symbol="ETH_USDT",
                strategy_id="TEST",
                signal_id=new_identity(),
                side="LONG",
                decision_timestamp=utc_now(),
            )
            duplicate = {**first, "sequence": 2}
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(duplicate) + "\n")
            with self.assertRaisesRegex(IntegrityError, "duplicate event_id"):
                scan_ledger(path, run_id)

            path.write_text(json.dumps(first) + "\n", encoding="utf-8")
            regression = {**first, "event_id": new_identity(), "sequence": 3}
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(regression) + "\n")
            with self.assertRaisesRegex(IntegrityError, "sequence regression/gap"):
                scan_ledger(path, run_id)

    def test_checkpoint_reconciliation_uses_decimal_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_id = new_identity()
            summary = scan_ledger(Path(directory) / "events.jsonl", run_id)
            state: dict[str, object] = {
                "schema_version": 3,
                "mode": "PAPER",
                "last_event_sequence": 0,
                "starting_equity_usdt": "300.00000000",
                "balance_usdt": "300.00000000",
                "cumulative_realized_pnl_usdt": "0",
                "fees_usdt": "0",
                "trades": 0,
                "position": None,
            }
            reconcile_checkpoint(state, summary)
            state["balance_usdt"] = "299.99999998"
            with self.assertRaisesRegex(IntegrityError, "equity mismatch"):
                reconcile_checkpoint(state, summary)

    def test_new_run_archives_legacy_evidence_and_never_overwrites_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            config = root / "paper.toml"
            logs.mkdir()
            config.write_text("[paper]\nmode='test'\n", encoding="utf-8")
            legacy_state = '{"schema_version":2,"balance_usdt":"292.99226546"}'
            (logs / "paper-state.json").write_text(legacy_state, encoding="utf-8")
            (logs / "paper-events.jsonl").write_text('{"event":"legacy"}\n', encoding="utf-8")

            metadata = create_new_run(
                project_root=root,
                config_path=config,
                log_directory=logs,
                starting_equity=Decimal("300"),
            )

            state = json.loads((logs / "paper-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["run_id"], metadata["run_id"])
            self.assertEqual(state["balance_usdt"], "300")
            archives = list((root / "data" / "runs").glob("legacy-*/legacy/paper-state.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_text(encoding="utf-8"), legacy_state)

    def test_tick_aware_quote_construction_and_invalid_raw_quotes(self) -> None:
        cases = (
            ("0.004392", "0.004393", "0.000001"),
            ("100.00", "100.01", "0.01"),
            ("50000.0", "50000.5", "0.1"),
        )
        for raw_bid, raw_ask, tick in cases:
            with self.subTest(raw_bid=raw_bid, tick=tick):
                bid, ask = construct_pessimistic_prices(
                    Decimal(raw_bid), Decimal(raw_ask), Decimal(tick), Decimal("2")
                )
                self.assertLess(bid, ask)
                self.assertEqual(bid % Decimal(tick), 0)
                self.assertEqual(ask % Decimal(tick), 0)
        for raw_bid, raw_ask in (("0", "1"), ("1", "0"), ("1", "1"), ("2", "1")):
            with self.subTest(raw_bid=raw_bid, raw_ask=raw_ask), self.assertRaises(
                QuoteValidationError
            ):
                construct_pessimistic_prices(
                    Decimal(raw_bid), Decimal(raw_ask), Decimal("0.01"), Decimal("2")
                )


if __name__ == "__main__":
    unittest.main()
