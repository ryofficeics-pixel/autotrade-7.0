from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

GATE_FUTURES_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
GATE_SIZE_DECIMAL_HEADER = {"X-Gate-Size-Decimal": "1"}
MAX_GATE_WS_MESSAGE_BYTES = 1_000_000
PUBLIC_GATE_CHANNELS = {
    "futures.trades",
    "futures.book_ticker",
    "futures.order_book_update",
}


class GateMarketDataError(ValueError):
    """Raised when public Gate market data is malformed or unsafe for replay."""


def _contract(value: object) -> str:
    symbol = str(value).strip().upper()
    base = symbol.removesuffix("_USDT")
    if not base or not base.isascii() or not base.isalnum() or symbol != f"{base}_USDT":
        raise GateMarketDataError("contract must be an ASCII BASE_USDT futures symbol")
    return symbol


def _decimal(value: object, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GateMarketDataError(f"{name} must be numeric") from exc
    if not number.is_finite():
        raise GateMarketDataError(f"{name} must be finite")
    return number


def _positive_decimal(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number <= 0:
        raise GateMarketDataError(f"{name} must be positive")
    return number


def _non_negative_decimal(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number < 0:
        raise GateMarketDataError(f"{name} must not be negative")
    return number


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise GateMarketDataError(f"{name} must be an integer")
    try:
        return int(str(value))
    except ValueError as exc:
        raise GateMarketDataError(f"{name} must be an integer") from exc


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_from_millis(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat().replace("+00:00", "Z")


def _message_time_ms(message: dict[str, Any]) -> int | None:
    if "time_ms" in message:
        return _integer(message["time_ms"], "time_ms")
    if "time" in message:
        return _integer(message["time"], "time") * 1000
    return None


def _trade_time_ms(trade: dict[str, Any], fallback: int | None) -> int | None:
    if "create_time_ms" in trade:
        return _integer(trade["create_time_ms"], "create_time_ms")
    if "create_time" in trade:
        return _integer(trade["create_time"], "create_time") * 1000
    return fallback


def _levels(value: object, name: str) -> list[dict[str, float]]:
    if not isinstance(value, list):
        raise GateMarketDataError(f"{name} must be a list")
    levels: list[dict[str, float]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise GateMarketDataError(f"{name}[{index}] must be an object")
        price = _positive_decimal(item.get("p"), f"{name}[{index}].p")
        size = _non_negative_decimal(item.get("s"), f"{name}[{index}].s")
        levels.append({"price": float(price), "size": float(size)})
    return levels


def build_gate_ws_subscriptions(
    symbols: tuple[str, ...] | list[str],
    *,
    unix_time: int | None = None,
    book_frequency: str = "100ms",
    book_level: str = "20",
) -> list[dict[str, object]]:
    contracts = [_contract(symbol) for symbol in symbols]
    if not contracts:
        raise GateMarketDataError("at least one contract is required")
    if book_frequency not in {"20ms", "100ms"}:
        raise GateMarketDataError("book_frequency must be 20ms or 100ms")
    if book_level not in {"20", "50", "100"}:
        raise GateMarketDataError("book_level must be 20, 50, or 100")
    if book_frequency == "20ms" and book_level != "20":
        raise GateMarketDataError("20ms order-book updates only support 20 levels")

    now = int(time.time()) if unix_time is None else unix_time
    requests: list[dict[str, object]] = [
        {
            "time": now,
            "channel": "futures.trades",
            "event": "subscribe",
            "payload": contracts,
        },
        {
            "time": now,
            "channel": "futures.book_ticker",
            "event": "subscribe",
            "payload": contracts,
        },
    ]
    requests.extend(
        {
            "time": now,
            "channel": "futures.order_book_update",
            "event": "subscribe",
            "payload": [contract, book_frequency, book_level],
        }
        for contract in contracts
    )
    return requests


def encode_gate_ws_request(request: dict[str, object]) -> str:
    return json.dumps(request, allow_nan=False, separators=(",", ":"))


def normalize_gate_ws_message(
    raw: str | bytes,
    *,
    local_receive_utc: datetime | None = None,
) -> list[dict[str, object]]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_GATE_WS_MESSAGE_BYTES:
            raise GateMarketDataError("Gate WebSocket message exceeded the safety limit")
        raw = raw.decode("utf-8")
    elif len(raw.encode("utf-8")) > MAX_GATE_WS_MESSAGE_BYTES:
        raise GateMarketDataError("Gate WebSocket message exceeded the safety limit")

    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateMarketDataError("Gate WebSocket message is not valid JSON") from exc
    if not isinstance(message, dict):
        raise GateMarketDataError("Gate WebSocket message must be an object")

    channel = str(message.get("channel", ""))
    event = str(message.get("event", ""))
    if channel not in PUBLIC_GATE_CHANNELS:
        return []
    if "error" in message:
        raise GateMarketDataError(f"Gate WebSocket {channel} returned an error")
    if event != "update":
        return []

    received = _utc_iso(local_receive_utc or datetime.now(UTC))
    message_exchange_ms = _message_time_ms(message)
    common = {
        "schema_version": 1,
        "source": "GATE_FUTURES_WS",
        "channel": channel,
        "event": event,
        "local_receive_utc": received,
        "message_exchange_time_ms": message_exchange_ms,
        "message_exchange_utc": _utc_from_millis(message_exchange_ms),
    }

    if channel == "futures.trades":
        return _normalize_trades(message, common, message_exchange_ms)
    if channel == "futures.book_ticker":
        return [_normalize_book_ticker(message, common, message_exchange_ms)]
    return [_normalize_order_book_update(message, common, message_exchange_ms)]


def _normalize_trades(
    message: dict[str, Any],
    common: dict[str, object],
    fallback_exchange_ms: int | None,
) -> list[dict[str, object]]:
    result = message.get("result")
    if not isinstance(result, list):
        raise GateMarketDataError("trades result must be a list")
    records: list[dict[str, object]] = []
    for item in result:
        if not isinstance(item, dict):
            raise GateMarketDataError("trade result must contain objects")
        contract = _contract(item.get("contract"))
        price = _positive_decimal(item.get("price"), "trade.price")
        size = _decimal(item.get("size"), "trade.size")
        if size == 0:
            raise GateMarketDataError("trade.size must not be zero")
        exchange_ms = _trade_time_ms(item, fallback_exchange_ms)
        records.append(
            {
                **common,
                "type": "trade",
                "contract": contract,
                "exchange_time_ms": exchange_ms,
                "exchange_utc": _utc_from_millis(exchange_ms),
                "trade_id": _integer(item.get("id"), "trade.id"),
                "price": float(price),
                "size": float(size),
                "side": "BUY" if size > 0 else "SELL",
                "is_internal": bool(item.get("is_internal", False)),
                "raw": item,
            }
        )
    return records


def _normalize_book_ticker(
    message: dict[str, Any],
    common: dict[str, object],
    fallback_exchange_ms: int | None,
) -> dict[str, object]:
    result = message.get("result")
    if not isinstance(result, dict):
        raise GateMarketDataError("book_ticker result must be an object")
    contract = _contract(result.get("s"))
    bid = _positive_decimal(result.get("b"), "book_ticker.b")
    ask = _positive_decimal(result.get("a"), "book_ticker.a")
    if ask < bid:
        raise GateMarketDataError("book_ticker ask must not be below bid")
    bid_size = _non_negative_decimal(result.get("B"), "book_ticker.B")
    ask_size = _non_negative_decimal(result.get("A"), "book_ticker.A")
    exchange_ms = _integer(result["t"], "book_ticker.t") if "t" in result else fallback_exchange_ms
    mid = (bid + ask) / 2
    return {
        **common,
        "type": "book_ticker",
        "contract": contract,
        "exchange_time_ms": exchange_ms,
        "exchange_utc": _utc_from_millis(exchange_ms),
        "update_id": _integer(result.get("u"), "book_ticker.u"),
        "bid": float(bid),
        "bid_size": float(bid_size),
        "ask": float(ask),
        "ask_size": float(ask_size),
        "spread_bps": float((ask - bid) / mid * 10_000),
        "raw": result,
    }


def _normalize_order_book_update(
    message: dict[str, Any],
    common: dict[str, object],
    fallback_exchange_ms: int | None,
) -> dict[str, object]:
    result = message.get("result")
    if not isinstance(result, dict):
        raise GateMarketDataError("order_book_update result must be an object")
    first_id = _integer(result.get("U"), "order_book_update.U")
    last_id = _integer(result.get("u"), "order_book_update.u")
    if last_id < first_id:
        raise GateMarketDataError("order_book_update.u must be >= U")
    exchange_ms = (
        _integer(result["t"], "order_book_update.t") if "t" in result else fallback_exchange_ms
    )
    return {
        **common,
        "type": "order_book_delta",
        "contract": _contract(result.get("s")),
        "exchange_time_ms": exchange_ms,
        "exchange_utc": _utc_from_millis(exchange_ms),
        "first_update_id": first_id,
        "last_update_id": last_id,
        "full": bool(result.get("full", False)),
        "level": str(result.get("l", "")),
        "bids": _levels(result.get("b", []), "order_book_update.b"),
        "asks": _levels(result.get("a", []), "order_book_update.a"),
        "raw": result,
    }


class GateOrderBookSequence:
    def __init__(self) -> None:
        self._last_update_id: dict[str, int] = {}

    def apply(self, record: dict[str, object]) -> str:
        if record.get("type") != "order_book_delta":
            return "IGNORED"
        contract = str(record.get("contract", ""))
        first_id = _integer(record.get("first_update_id"), "first_update_id")
        last_id = _integer(record.get("last_update_id"), "last_update_id")
        if bool(record.get("full", False)):
            self._last_update_id[contract] = last_id
            return "RESET"

        expected_previous = self._last_update_id.get(contract)
        self._last_update_id[contract] = last_id
        if expected_previous is None:
            return "BOOTSTRAP"
        if first_id != expected_previous + 1:
            self._last_update_id.pop(contract, None)
            return "GAP"
        return "OK"


def append_gate_ws_records(path: Path, records: list[dict[str, object]]) -> int:
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, allow_nan=False, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return len(records)
