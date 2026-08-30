from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from autotrade.market_data import (
    GATE_FUTURES_WS_URL,
    GATE_SIZE_DECIMAL_HEADER,
    GateMarketDataError,
    GateOrderBookSequence,
    append_gate_ws_records,
    build_gate_ws_subscriptions,
    encode_gate_ws_request,
    normalize_gate_ws_message,
)


class GateMarketDataTests(unittest.TestCase):
    def test_subscription_payloads_use_public_decimal_gate_channels(self) -> None:
        requests = build_gate_ws_subscriptions(("btc_usdt", "ETH_USDT"), unix_time=123456)

        self.assertEqual(GATE_FUTURES_WS_URL, "wss://fx-ws.gateio.ws/v4/ws/usdt")
        self.assertEqual(GATE_SIZE_DECIMAL_HEADER, {"X-Gate-Size-Decimal": "1"})
        self.assertEqual(
            requests,
            [
                {
                    "time": 123456,
                    "channel": "futures.trades",
                    "event": "subscribe",
                    "payload": ["BTC_USDT", "ETH_USDT"],
                },
                {
                    "time": 123456,
                    "channel": "futures.book_ticker",
                    "event": "subscribe",
                    "payload": ["BTC_USDT", "ETH_USDT"],
                },
                {
                    "time": 123456,
                    "channel": "futures.order_book_update",
                    "event": "subscribe",
                    "payload": ["BTC_USDT", "100ms", "20"],
                },
                {
                    "time": 123456,
                    "channel": "futures.order_book_update",
                    "event": "subscribe",
                    "payload": ["ETH_USDT", "100ms", "20"],
                },
            ],
        )
        self.assertEqual(
            encode_gate_ws_request(requests[0]),
            '{"time":123456,"channel":"futures.trades","event":"subscribe",'
            '"payload":["BTC_USDT","ETH_USDT"]}',
        )

    def test_invalid_subscription_parameters_fail_closed(self) -> None:
        with self.assertRaisesRegex(GateMarketDataError, "BASE_USDT"):
            build_gate_ws_subscriptions(("BTC/USDT",))
        with self.assertRaisesRegex(GateMarketDataError, "20ms"):
            build_gate_ws_subscriptions(("BTC_USDT",), book_frequency="20ms", book_level="100")

    def test_trade_updates_preserve_exchange_and_local_timestamps(self) -> None:
        records = normalize_gate_ws_message(
            json.dumps(
                {
                    "channel": "futures.trades",
                    "event": "update",
                    "time": 1541503698,
                    "time_ms": 1541503698123,
                    "result": [
                        {
                            "size": "-108.5",
                            "id": 27753479,
                            "create_time": 1545136464,
                            "create_time_ms": 1545136464123,
                            "price": "96.4",
                            "contract": "BTC_USDT",
                            "is_internal": True,
                        }
                    ],
                }
            ),
            local_receive_utc=datetime(2026, 8, 30, 10, 29, 20, tzinfo=UTC),
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["type"], "trade")
        self.assertEqual(records[0]["contract"], "BTC_USDT")
        self.assertEqual(records[0]["exchange_time_ms"], 1545136464123)
        self.assertEqual(records[0]["local_receive_utc"], "2026-08-30T10:29:20Z")
        self.assertEqual(records[0]["side"], "SELL")
        self.assertEqual(records[0]["is_internal"], True)

    def test_book_ticker_updates_reject_crossed_or_empty_books(self) -> None:
        records = normalize_gate_ws_message(
            json.dumps(
                {
                    "channel": "futures.book_ticker",
                    "event": "update",
                    "time_ms": 1615366379123,
                    "result": {
                        "t": 1615366379123,
                        "u": 2517661076,
                        "s": "BTC_USDT",
                        "b": "54696.6",
                        "B": "37000.5",
                        "a": "54696.7",
                        "A": "47061.2",
                    },
                }
            )
        )

        self.assertEqual(records[0]["type"], "book_ticker")
        spread_bps = records[0]["spread_bps"]
        assert isinstance(spread_bps, float)
        self.assertAlmostEqual(spread_bps, 0.01828265)

        with self.assertRaisesRegex(GateMarketDataError, "numeric"):
            normalize_gate_ws_message(
                json.dumps(
                    {
                        "channel": "futures.book_ticker",
                        "event": "update",
                        "result": {"s": "BTC_USDT", "b": "", "B": "0", "a": "1", "A": "1"},
                    }
                )
            )

    def test_order_book_update_records_sequence_fields_and_levels(self) -> None:
        records = normalize_gate_ws_message(
            json.dumps(
                {
                    "time_ms": 1615366381123,
                    "channel": "futures.order_book_update",
                    "event": "update",
                    "result": {
                        "t": 1615366381417,
                        "s": "BTC_USDT",
                        "U": 2517661101,
                        "u": 2517661113,
                        "b": [{"p": "54672.1", "s": "0"}],
                        "a": [{"p": "54743.6", "s": "95.2"}],
                        "l": "100",
                        "full": True,
                    },
                }
            )
        )

        self.assertEqual(records[0]["type"], "order_book_delta")
        self.assertEqual(records[0]["first_update_id"], 2517661101)
        self.assertEqual(records[0]["last_update_id"], 2517661113)
        self.assertEqual(records[0]["full"], True)
        self.assertEqual(records[0]["bids"], [{"price": 54672.1, "size": 0.0}])

    def test_order_book_sequence_tracker_flags_gaps(self) -> None:
        tracker = GateOrderBookSequence()
        full = {
            "type": "order_book_delta",
            "contract": "BTC_USDT",
            "first_update_id": 100,
            "last_update_id": 110,
            "full": True,
        }
        self.assertEqual(tracker.apply(full), "RESET")
        self.assertEqual(
            tracker.apply({**full, "first_update_id": 111, "last_update_id": 112, "full": False}),
            "OK",
        )
        self.assertEqual(
            tracker.apply({**full, "first_update_id": 114, "last_update_id": 115, "full": False}),
            "GAP",
        )
        self.assertEqual(
            tracker.apply({**full, "first_update_id": 116, "last_update_id": 117, "full": False}),
            "BOOTSTRAP",
        )

    def test_append_records_writes_replay_friendly_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate-ws.jsonl"
            count = append_gate_ws_records(
                path,
                [{"schema_version": 1, "type": "trade", "contract": "BTC_USDT"}],
            )

            self.assertEqual(count, 1)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"schema_version": 1, "type": "trade", "contract": "BTC_USDT"},
            )

    def test_non_update_acks_are_ignored_and_errors_are_not_silent(self) -> None:
        self.assertEqual(
            normalize_gate_ws_message(
                '{"time":1545405058,"channel":"futures.trades","event":"subscribe",'
                '"result":{"status":"success"}}'
            ),
            [],
        )
        with self.assertRaisesRegex(GateMarketDataError, "returned an error"):
            normalize_gate_ws_message(
                '{"channel":"futures.trades","event":"subscribe","error":{"message":"bad"}}'
            )


if __name__ == "__main__":
    unittest.main()
