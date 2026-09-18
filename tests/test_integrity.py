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
    commit_transition,
    create_new_run,
    new_identity,
    reconcile_checkpoint,
    recover_pending_transition,
    scan_ledger,
    utc_now,
)
from autotrade.paper import QuoteValidationError, construct_pessimistic_prices


class IntegrityTests(unittest.TestCase):
    @staticmethod
    def _open_transition(
        root: Path,
    ) -> tuple[EventLedger, Path, dict[str, object], list[tuple[str, dict[str, object]]]]:
        config = root / "paper.toml"
        config.write_text("[paper]\nmode='test'\n", encoding="utf-8")
        metadata = create_new_run(
            project_root=root,
            config_path=config,
            log_directory=root,
            starting_equity=Decimal("300"),
        )
        state_path = root / "paper-state.json"
        state: dict[str, object] = json.loads(state_path.read_text(encoding="utf-8"))
        ledger = EventLedger(
            root / str(state["event_ledger_path"]),
            str(metadata["run_id"]),
            new_identity(),
        )
        signal_id = new_identity()
        position_id = new_identity()
        order_id = new_identity()
        now = utc_now()
        specs: list[tuple[str, dict[str, object]]] = [
            (
                "signal",
                {
                    "symbol": "ETH_USDT",
                    "strategy_id": "TEST",
                    "signal_id": signal_id,
                    "side": "LONG",
                    "decision_timestamp": now,
                },
            ),
            (
                "order_submitted",
                {
                    "symbol": "ETH_USDT",
                    "strategy_id": "TEST",
                    "signal_id": signal_id,
                    "order_id": order_id,
                    "client_order_id": "TEST-ENTRY",
                    "side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "0.1",
                    "order_submit_timestamp": now,
                },
            ),
            (
                "fill",
                {
                    "symbol": "ETH_USDT",
                    "strategy_id": "TEST",
                    "signal_id": signal_id,
                    "position_id": position_id,
                    "order_id": order_id,
                    "client_order_id": "TEST-ENTRY",
                    "fill_id": new_identity(),
                    "side": "BUY",
                    "quantity": "0.1",
                    "price": "100",
                    "fee_amount": "0.005",
                    "fee_currency": "USDT",
                    "fill_timestamp": now,
                },
            ),
            (
                "position_opened",
                {
                    "symbol": "ETH_USDT",
                    "strategy_id": "TEST",
                    "signal_id": signal_id,
                    "position_id": position_id,
                    "side": "LONG",
                    "quantity": "0.1",
                    "price": "100",
                },
            ),
        ]
        checkpoint = {
            **state,
            "checkpoint_sequence": 1,
            "balance_usdt": "299.995",
            "fees_usdt": "0.005",
            "position": {
                "symbol": "ETH_USDT",
                "side": "LONG",
                "quantity": "0.1",
                "entry_price": "100",
                "entry_fee_usdt": "0.005",
                "position_id": position_id,
                "signal_id": signal_id,
            },
        }
        return ledger, state_path, checkpoint, specs

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

    def test_transition_recovers_every_persistence_boundary(self) -> None:
        boundaries = (
            "before_event_append",
            "after_event_append",
            "before_checkpoint_write",
            "during_temporary_checkpoint_write",
            "after_atomic_rename",
            "before_commit_marker",
            "after_commit_marker",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger, state_path, checkpoint, specs = self._open_transition(root)

                def fail(current: str, *, crash_boundary: str = boundary) -> None:
                    if current == crash_boundary:
                        raise OSError(f"crash at {crash_boundary}")

                with self.assertRaisesRegex(OSError, boundary):
                    commit_transition(
                        ledger,
                        state_path,
                        checkpoint,
                        specs,
                        failure_injector=fail,
                    )
                recovered = recover_pending_transition(
                    state_path,
                    ledger.path,
                    ledger.run_id,
                )
                self.assertIsNotNone(recovered)
                summary = scan_ledger(ledger.path, ledger.run_id)
                recovered_state = json.loads(state_path.read_text(encoding="utf-8"))
                reconcile_checkpoint(recovered_state, summary)
                self.assertEqual(recovered_state["position"]["quantity"], "0.1")
                self.assertEqual(
                    recovered_state["transition_recovery"]["status"], "REPLAYED_COMMIT"
                )
                self.assertFalse(state_path.with_suffix(".json.transition").exists())

    def test_unexplained_sequence_mismatch_and_duplicate_commit_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger, state_path, checkpoint, specs = self._open_transition(root)
            commit_transition(ledger, state_path, checkpoint, specs)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["last_event_sequence"] = int(state["last_event_sequence"]) + 1
            with self.assertRaisesRegex(IntegrityError, "sequence mismatch"):
                reconcile_checkpoint(state, ledger.summary)

            committed = json.loads(ledger.path.read_text(encoding="utf-8").splitlines()[-1])
            committed["sequence"] = int(committed["sequence"]) + 1
            committed["event_id"] = new_identity()
            committed["target_event_sequence"] = committed["sequence"]
            with ledger.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(committed) + "\n")
            with self.assertRaisesRegex(IntegrityError, "invalid transition commit"):
                scan_ledger(ledger.path, ledger.run_id)

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
            with (
                self.subTest(raw_bid=raw_bid, raw_ask=raw_ask),
                self.assertRaises(QuoteValidationError),
            ):
                construct_pessimistic_prices(
                    Decimal(raw_bid), Decimal(raw_ask), Decimal("0.01"), Decimal("2")
                )


if __name__ == "__main__":
    unittest.main()
