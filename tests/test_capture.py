from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autotrade.capture import (
    BookSequenceTracker,
    DatasetError,
    GateMarketCapture,
    RawEventRecorder,
    iter_replay_events,
    verify_dataset,
)


class MarketDataTests(unittest.TestCase):
    def test_book_sequence_tracker_reconciles_snapshot_and_detects_gap(self) -> None:
        tracker = BookSequenceTracker()
        self.assertEqual(tracker.delta(101, 102), "BUFFERED")
        self.assertEqual(tracker.snapshot(100), "SYNCED")
        self.assertEqual(tracker.last_update_id, 102)
        self.assertEqual(tracker.delta(103, 104), "APPLIED")
        self.assertEqual(tracker.delta(103, 104), "DUPLICATE")
        self.assertEqual(tracker.delta(106, 107), "GAP")
        self.assertEqual(tracker.gaps, 1)
        self.assertEqual(tracker.snapshot(105), "SYNCED")
        self.assertEqual(tracker.last_update_id, 107)

    def test_hash_chained_dataset_replays_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RawEventRecorder(
                Path(directory),
                ("ETH_USDT",),
                code_revision="abc123",
                config_revision="def456",
            )
            recorder.append(
                source="GATE_FUTURES_WS",
                channel="futures.book_ticker",
                event="update",
                symbol="ETH_USDT",
                exchange_ts_ms=1_000,
                received_ts_ns=1_001_000_000,
                connection_id=1,
                payload={"result": {"b": "100", "a": "101"}},
            )
            recorder.append(
                source="GATE_FUTURES_WS",
                channel="futures.trades",
                event="update",
                symbol="ETH_USDT",
                exchange_ts_ms=1_002,
                received_ts_ns=1_003_000_000,
                connection_id=1,
                payload={"result": [{"price": "100.5"}]},
            )
            manifest = recorder.close(
                complete=True,
                connections=1,
                gaps=0,
                duplicates=0,
                resnapshots=1,
            )
            dataset = manifest.parent
            first = list(iter_replay_events(dataset))
            second = list(iter_replay_events(dataset))
            self.assertEqual(first, second)
            self.assertEqual(verify_dataset(dataset)["event_count"], 2)
            self.assertEqual(first[0]["local_sequence"], 1)
            self.assertEqual(first[1]["previous_hash"], first[0]["event_hash"])

    def test_replay_rejects_modified_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RawEventRecorder(
                Path(directory),
                ("ETH_USDT",),
                code_revision="abc123",
                config_revision="def456",
            )
            recorder.append(
                source="GATE_FUTURES_WS",
                channel="futures.book_ticker",
                event="update",
                symbol="ETH_USDT",
                exchange_ts_ms=1_000,
                received_ts_ns=1_001_000_000,
                connection_id=1,
                payload={"result": {"b": "100", "a": "101"}},
            )
            manifest = recorder.close(
                complete=True,
                connections=1,
                gaps=0,
                duplicates=0,
                resnapshots=1,
            )
            events_path = manifest.parent / "events.jsonl"
            event = json.loads(events_path.read_text(encoding="utf-8"))
            event["channel"] = "futures.tickers"
            events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(DatasetError, "event hash mismatch"):
                list(iter_replay_events(manifest.parent))

    def test_order_book_subscription_ack_is_not_parsed_as_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RawEventRecorder(
                Path(directory),
                ("ETH_USDT",),
                code_revision="abc123",
                config_revision="def456",
            )
            capture = GateMarketCapture(("ETH_USDT",), recorder)
            result = capture._record_ws_message(  # noqa: SLF001 - protocol regression test
                {
                    "channel": "futures.order_book_update",
                    "event": "subscribe",
                    "time_ms": 1000,
                    "result": {"status": "success"},
                },
                received_ns=1_001_000_000,
                connection_id=1,
            )
            self.assertIsNone(result)
            recorder.close(
                complete=False,
                connections=1,
                gaps=0,
                duplicates=0,
                resnapshots=0,
            )


if __name__ == "__main__":
    unittest.main()
