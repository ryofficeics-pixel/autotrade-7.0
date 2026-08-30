from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from autotrade.market_data import (
    GATE_FUTURES_WS_URL,
    GATE_SIZE_DECIMAL_HEADER,
    MAX_GATE_WS_MESSAGE_BYTES,
    GateLocalOrderBook,
    GateMarketDataError,
    build_gate_ws_subscriptions,
    normalize_gate_ws_message,
)

GATE_FUTURES_REST_URL = "https://api.gateio.ws/api/v4/futures/usdt/order_book"
EVENT_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
CAPTURE_QUEUE_SIZE = 4096
CAPTURE_STALE_SECONDS = 20.0
BOOK_LEVELS = "20"
BOOK_FREQUENCY = "100ms"


class DatasetError(RuntimeError):
    """Raised when a capture or replay dataset fails an integrity check."""


@dataclass
class BookSequenceTracker:
    """Tracks Gate snapshot/delta IDs without pretending to reconstruct depth yet."""

    last_update_id: int | None = None
    gaps: int = 0
    duplicates: int = 0
    resnapshots: int = 0
    _buffer: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=10_000))

    def reconnect(self) -> None:
        self.last_update_id = None
        self._buffer.clear()

    def delta(self, first_id: int, last_id: int) -> str:
        if first_id <= 0 or last_id < first_id:
            raise DatasetError("invalid Gate order-book update IDs")
        if self.last_update_id is None:
            if len(self._buffer) == self._buffer.maxlen:
                raise DatasetError("order-book synchronization buffer exhausted")
            self._buffer.append((first_id, last_id))
            return "BUFFERED"
        expected = self.last_update_id + 1
        if last_id < expected:
            self.duplicates += 1
            return "DUPLICATE"
        if first_id <= expected <= last_id:
            self.last_update_id = last_id
            return "APPLIED"
        self.gaps += 1
        self.last_update_id = None
        self._buffer.clear()
        self._buffer.append((first_id, last_id))
        return "GAP"

    def snapshot(self, base_id: int) -> str:
        if base_id <= 0:
            raise DatasetError("invalid Gate order-book snapshot ID")
        self.last_update_id = base_id
        buffered = tuple(self._buffer)
        self._buffer.clear()
        self.resnapshots += 1
        status = "SYNCED"
        for first_id, last_id in buffered:
            outcome = self.delta(first_id, last_id)
            if outcome == "GAP":
                status = "GAP"
                break
        return status


class RawEventRecorder:
    """Writes one append-only, hash-chained Gate capture session."""

    def __init__(
        self,
        root: Path,
        symbols: Sequence[str],
        *,
        code_revision: str,
        config_revision: str,
    ) -> None:
        started = datetime.now(UTC)
        prefix = started.strftime("%Y%m%dT%H%M%S.%fZ")
        suffix = hashlib.sha256(f"{prefix}|{os.getpid()}".encode()).hexdigest()[:8]
        self.dataset_id = f"gate-usdt-{prefix}-{suffix}"
        self.path = root / self.dataset_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.events_path = self.path / "events.jsonl"
        self.manifest_path = self.path / "manifest.json"
        self._file = self.events_path.open("x", encoding="utf-8", newline="\n")
        self._symbols = tuple(symbols)
        self._started_utc = started.isoformat().replace("+00:00", "Z")
        self._code_revision = code_revision
        self._config_revision = config_revision
        self._count = 0
        self._last_hash = "0" * 64
        self._closed = False
        self._first_exchange_ts_ms: int | None = None
        self._last_exchange_ts_ms: int | None = None
        self._latency_total_ms = 0.0
        self._latency_samples = 0
        self._latency_max_ms = 0.0

    def append(
        self,
        *,
        source: str,
        channel: str,
        event: str,
        symbol: str | None,
        exchange_ts_ms: int | None,
        received_ts_ns: int,
        connection_id: int,
        payload: object,
    ) -> None:
        if self._closed:
            raise DatasetError("capture dataset is already closed")
        self._count += 1
        record: dict[str, object] = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "local_sequence": self._count,
            "source": source,
            "channel": channel,
            "event": event,
            "symbol": symbol,
            "exchange_ts_ms": exchange_ts_ms,
            "received_ts_ns": received_ts_ns,
            "connection_id": connection_id,
            "previous_hash": self._last_hash,
            "payload": payload,
        }
        canonical = _canonical_json(record)
        event_hash = hashlib.sha256(canonical.encode()).hexdigest()
        self._file.write(_canonical_json({**record, "event_hash": event_hash}) + "\n")
        self._file.flush()
        if self._count % 256 == 0:
            os.fsync(self._file.fileno())
        self._last_hash = event_hash
        if exchange_ts_ms is not None:
            self._first_exchange_ts_ms = (
                exchange_ts_ms
                if self._first_exchange_ts_ms is None
                else min(self._first_exchange_ts_ms, exchange_ts_ms)
            )
            self._last_exchange_ts_ms = (
                exchange_ts_ms
                if self._last_exchange_ts_ms is None
                else max(self._last_exchange_ts_ms, exchange_ts_ms)
            )
            latency_ms = received_ts_ns / 1_000_000 - exchange_ts_ms
            if latency_ms >= 0:
                self._latency_total_ms += latency_ms
                self._latency_samples += 1
                self._latency_max_ms = max(self._latency_max_ms, latency_ms)

    def close(
        self,
        *,
        complete: bool,
        connections: int,
        gaps: int,
        duplicates: int,
        resnapshots: int,
        error: str | None = None,
    ) -> Path:
        if self._closed:
            return self.manifest_path
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._closed = True
        manifest: dict[str, object] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "source": "GATE_FUTURES_PUBLIC",
            "venue": "GATE",
            "product": "USDT_PERPETUAL",
            "symbols": list(self._symbols),
            "started_utc": self._started_utc,
            "ended_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "first_exchange_ts_ms": self._first_exchange_ts_ms,
            "last_exchange_ts_ms": self._last_exchange_ts_ms,
            "event_count": self._count,
            "dropped_events": 0,
            "sequence_gaps": gaps,
            "duplicates": duplicates,
            "resnapshots": resnapshots,
            "connections": connections,
            "integrity_status": "COMPLETE"
            if complete and gaps == 0 and connections == 1
            else "DEGRADED",
            "complete": complete,
            "error": error,
            "final_event_hash": self._last_hash,
            "event_file_bytes": self.events_path.stat().st_size,
            "latency_samples": self._latency_samples,
            "average_observed_latency_ms": round(
                self._latency_total_ms / self._latency_samples, 3
            )
            if self._latency_samples
            else None,
            "maximum_observed_latency_ms": round(self._latency_max_ms, 3)
            if self._latency_samples
            else None,
            "code_revision": self._code_revision,
            "config_revision": self._config_revision,
            "strategy_revision": "REST_MOMENTUM_BASELINE_V1",
            "simulator_version": "NAUTILUS_1.231.0_REST_PAPER_V1",
            "random_seed": None,
        }
        _atomic_json(self.manifest_path, manifest)
        return self.manifest_path


class GateMarketCapture:
    """Captures public Gate futures data without influencing the paper engine."""

    def __init__(self, symbols: Sequence[str], recorder: RawEventRecorder) -> None:
        normalized = tuple(dict.fromkeys(symbols))
        if not normalized or len(normalized) > 10:
            raise ValueError("capture requires between 1 and 10 symbols")
        if any(not symbol.endswith("_USDT") or len(symbol) > 32 for symbol in normalized):
            raise ValueError("capture symbol is invalid")
        self.symbols = normalized
        self.recorder = recorder
        self.trackers = {symbol: BookSequenceTracker() for symbol in normalized}
        self.connections = 0

    async def run(self, duration_seconds: int) -> Path:
        if not 10 <= duration_seconds <= 86_400:
            raise ValueError("capture duration must be between 10 and 86400 seconds")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration_seconds
        complete = False
        error: str | None = None
        try:
            async for websocket in connect(
                GATE_FUTURES_WS_URL,
                additional_headers=GATE_SIZE_DECIMAL_HEADER,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=MAX_GATE_WS_MESSAGE_BYTES,
                max_queue=64,
                proxy=None,
            ):
                self.connections += 1
                try:
                    await self._connection(websocket, self.connections, deadline)
                except (DatasetError, ConnectionClosed, OSError, TimeoutError) as exc:
                    self.recorder.append(
                        source="AUTOTRADE_CAPTURE",
                        channel="capture.connection",
                        event="disconnected",
                        symbol=None,
                        exchange_ts_ms=None,
                        received_ts_ns=time.time_ns(),
                        connection_id=self.connections,
                        payload={"reason": str(exc)[:200]},
                    )
                    delay = min(1.0, max(0.0, deadline - loop.time()))
                    if delay:
                        await asyncio.sleep(delay)
                if loop.time() >= deadline:
                    complete = True
                    break
        except asyncio.CancelledError:
            error = "capture cancelled"
            raise
        except Exception as exc:
            error = str(exc)[:300]
            raise
        finally:
            gaps = sum(tracker.gaps for tracker in self.trackers.values())
            duplicates = sum(tracker.duplicates for tracker in self.trackers.values())
            resnapshots = sum(tracker.resnapshots for tracker in self.trackers.values())
            synchronized = all(
                tracker.last_update_id is not None for tracker in self.trackers.values()
            )
            if complete and not synchronized:
                complete = False
                error = error or "one or more order books never synchronized"
            manifest = self.recorder.close(
                complete=complete,
                connections=self.connections,
                gaps=gaps,
                duplicates=duplicates,
                resnapshots=resnapshots,
                error=error,
            )
        return manifest

    async def _connection(
        self,
        websocket: ClientConnection,
        connection_id: int,
        deadline: float,
    ) -> None:
        self.recorder.append(
            source="AUTOTRADE_CAPTURE",
            channel="capture.connection",
            event="connected",
            symbol=None,
            exchange_ts_ms=None,
            received_ts_ns=time.time_ns(),
            connection_id=connection_id,
            payload={"endpoint": GATE_FUTURES_WS_URL},
        )
        for tracker in self.trackers.values():
            tracker.reconnect()
        for request in _subscription_requests(self.symbols):
            await websocket.send(_canonical_json(request))

        queue: asyncio.Queue[tuple[str, int, object]] = asyncio.Queue(CAPTURE_QUEUE_SIZE)
        receiver = asyncio.create_task(self._receive(websocket, queue))
        snapshot_tasks = {
            asyncio.create_task(self._snapshot_to_queue(symbol, queue)) for symbol in self.symbols
        }
        try:
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                timeout = min(CAPTURE_STALE_SECONDS, max(0.01, remaining))
                deadline_limited = remaining <= CAPTURE_STALE_SECONDS
                try:
                    kind, received_ns, payload = await asyncio.wait_for(queue.get(), timeout)
                except TimeoutError:
                    if deadline_limited or asyncio.get_running_loop().time() >= deadline:
                        return
                    raise DatasetError("Gate WebSocket stream became stale") from None
                if kind == "error":
                    raise DatasetError(str(payload))
                if kind == "snapshot":
                    if not isinstance(payload, tuple) or len(payload) != 2:
                        raise DatasetError("internal snapshot event is invalid")
                    symbol_value, snapshot_value = payload
                    snapshot_gap = self._record_snapshot(
                        str(symbol_value), snapshot_value, received_ns, connection_id
                    )
                    if snapshot_gap:
                        snapshot_tasks.add(
                            asyncio.create_task(
                                self._snapshot_to_queue(str(symbol_value), queue)
                            )
                        )
                else:
                    gap_symbol = self._record_ws_message(payload, received_ns, connection_id)
                    if gap_symbol is not None:
                        snapshot_tasks.add(
                            asyncio.create_task(self._snapshot_to_queue(gap_symbol, queue))
                        )
                if receiver.done():
                    receiver.result()
                for task in tuple(snapshot_tasks):
                    if task.done():
                        task.result()
                        snapshot_tasks.remove(task)
        finally:
            receiver.cancel()
            for task in snapshot_tasks:
                task.cancel()
            await asyncio.gather(receiver, *snapshot_tasks, return_exceptions=True)

    @staticmethod
    async def _receive(
        websocket: ClientConnection,
        queue: asyncio.Queue[tuple[str, int, object]],
    ) -> None:
        async for message in websocket:
            received_ns = time.time_ns()
            try:
                parsed = json.loads(message)
            except (json.JSONDecodeError, TypeError) as exc:
                await queue.put(("error", received_ns, f"invalid Gate JSON: {exc}"))
                return
            await queue.put(("ws", received_ns, parsed))
        await queue.put(("error", time.time_ns(), "Gate WebSocket connection closed"))

    @staticmethod
    async def _snapshot_to_queue(
        symbol: str,
        queue: asyncio.Queue[tuple[str, int, object]],
    ) -> None:
        try:
            snapshot, received_ns = await asyncio.to_thread(_fetch_book_snapshot, symbol)
            await queue.put(("snapshot", received_ns, (symbol, snapshot)))
        except Exception as exc:
            await queue.put(("error", time.time_ns(), f"snapshot {symbol}: {exc}"))

    def _record_snapshot(
        self,
        symbol: str,
        snapshot: object,
        received_ns: int,
        connection_id: int,
    ) -> bool:
        if not isinstance(snapshot, dict):
            raise DatasetError("Gate order-book snapshot is not an object")
        base_id = _positive_int(snapshot.get("id"), "snapshot id")
        status = self.trackers[symbol].snapshot(base_id)
        exchange_ts_ms = _timestamp_ms(snapshot.get("current"))
        self.recorder.append(
            source="GATE_FUTURES_REST",
            channel="futures.order_book_snapshot",
            event="snapshot",
            symbol=symbol,
            exchange_ts_ms=exchange_ts_ms,
            received_ts_ns=received_ns,
            connection_id=connection_id,
            payload=snapshot,
        )
        return status == "GAP"

    def _record_ws_message(
        self,
        message: object,
        received_ns: int,
        connection_id: int,
    ) -> str | None:
        if not isinstance(message, dict):
            raise DatasetError("Gate message is not an object")
        channel = message.get("channel")
        event = message.get("event")
        if not isinstance(channel, str) or not channel.startswith("futures."):
            raise DatasetError("Gate message channel is invalid")
        if not isinstance(event, str) or event not in {"subscribe", "update", "unsubscribe"}:
            raise DatasetError("Gate message event is invalid")
        received_utc = datetime.fromtimestamp(received_ns / 1_000_000_000, UTC)
        try:
            records = normalize_gate_ws_message(
                _canonical_json(message), local_receive_utc=received_utc
            )
        except GateMarketDataError as exc:
            raise DatasetError(str(exc)) from exc
        symbols = {
            str(record["contract"])
            for record in records
            if isinstance(record.get("contract"), str)
        }
        symbol = next(iter(symbols)) if len(symbols) == 1 else "MULTI" if symbols else None
        exchange_times = [
            int(str(record["exchange_time_ms"]))
            for record in records
            if record.get("exchange_time_ms") is not None
        ]
        exchange_ts_ms = (
            max(exchange_times)
            if exchange_times
            else _optional_int(message.get("time_ms"))
        )
        self.recorder.append(
            source="GATE_FUTURES_WS",
            channel=channel,
            event=event,
            symbol=symbol,
            exchange_ts_ms=exchange_ts_ms,
            received_ts_ns=received_ns,
            connection_id=connection_id,
            payload=message,
        )
        if channel != "futures.order_book_update" or event != "update":
            return None
        if len(records) != 1 or records[0].get("type") != "order_book_delta":
            raise DatasetError("Gate order-book update did not normalize to one delta")
        record = records[0]
        symbol = str(record.get("contract", ""))
        if symbol not in self.trackers:
            raise DatasetError("Gate order-book update lacks a monitored symbol")
        first_id = _positive_int(record.get("first_update_id"), "order-book first update id")
        last_id = _positive_int(record.get("last_update_id"), "order-book last update id")
        return symbol if self.trackers[symbol].delta(first_id, last_id) == "GAP" else None


def iter_replay_events(dataset: Path) -> Iterator[dict[str, object]]:
    manifest = _read_manifest(dataset)
    expected_count = int(str(manifest["event_count"]))
    expected_final_hash = str(manifest["final_event_hash"])
    previous_hash = "0" * 64
    count = 0
    with (dataset / "events.jsonl").open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"invalid event JSON at line {line_number}") from exc
            if not isinstance(event, dict):
                raise DatasetError(f"event line {line_number} is not an object")
            count += 1
            event_hash = event.pop("event_hash", None)
            if event.get("local_sequence") != count:
                raise DatasetError(f"non-contiguous local sequence at line {line_number}")
            if event.get("previous_hash") != previous_hash:
                raise DatasetError(f"broken hash chain at line {line_number}")
            calculated = hashlib.sha256(_canonical_json(event).encode()).hexdigest()
            if event_hash != calculated:
                raise DatasetError(f"event hash mismatch at line {line_number}")
            previous_hash = calculated
            yield {**event, "event_hash": event_hash}
    if count != expected_count:
        raise DatasetError("manifest event count does not match events file")
    if previous_hash != expected_final_hash:
        raise DatasetError("manifest final hash does not match events file")


def verify_dataset(dataset: Path) -> dict[str, object]:
    event_count = sum(1 for _ in iter_replay_events(dataset))
    manifest = _read_manifest(dataset)
    result: dict[str, object] = {
        "dataset_id": manifest["dataset_id"],
        "event_count": event_count,
        "integrity_status": manifest["integrity_status"],
        "final_event_hash": manifest["final_event_hash"],
    }
    books = replay_order_books(dataset)
    if books:
        records = [books[symbol].metrics().replay_record() for symbol in sorted(books)]
        result["synchronized_books"] = len(records)
        result["book_digest"] = hashlib.sha256(_canonical_json(records).encode()).hexdigest()
    return result


def replay_order_books(dataset: Path) -> dict[str, GateLocalOrderBook]:
    """Rebuild final valid books from an integrity-verified capture session."""

    books: dict[str, GateLocalOrderBook] = {}
    pending: dict[str, deque[dict[str, object]]] = {}
    saw_snapshot = False
    for event in iter_replay_events(dataset):
        channel = event.get("channel")
        payload = event.get("payload")
        if channel == "futures.order_book_snapshot":
            saw_snapshot = True
            symbol = str(event.get("symbol", ""))
            book = books.setdefault(symbol, GateLocalOrderBook(symbol))
            book.apply_snapshot(payload)
            buffered = pending.pop(symbol, deque())
            while buffered:
                record = buffered.popleft()
                outcome = book.apply_delta(record)
                if outcome == "GAP":
                    retry = deque((record,), maxlen=10_000)
                    retry.extend(buffered)
                    pending[symbol] = retry
                    break
            continue
        if channel != "futures.order_book_update" or event.get("event") != "update":
            continue
        received_ns = int(str(event.get("received_ts_ns")))
        received_utc = datetime.fromtimestamp(received_ns / 1_000_000_000, UTC)
        try:
            records = normalize_gate_ws_message(
                _canonical_json(payload), local_receive_utc=received_utc
            )
        except GateMarketDataError as exc:
            raise DatasetError(str(exc)) from exc
        if len(records) != 1 or records[0].get("type") != "order_book_delta":
            raise DatasetError("replay order-book event did not normalize to one delta")
        record = records[0]
        symbol = str(record.get("contract", ""))
        book = books.setdefault(symbol, GateLocalOrderBook(symbol))
        if not book.synchronized:
            queue = pending.setdefault(symbol, deque(maxlen=10_000))
            if len(queue) == queue.maxlen:
                raise DatasetError("replay order-book synchronization buffer exhausted")
            queue.append(record)
            continue
        outcome = book.apply_delta(record)
        if outcome == "GAP":
            pending[symbol] = deque((record,), maxlen=10_000)

    synchronized = {symbol: book for symbol, book in books.items() if book.synchronized}
    if saw_snapshot and pending:
        missing = ", ".join(sorted(pending))
        raise DatasetError(f"replay ended with unsynchronized order books: {missing}")
    return synchronized


def capture_dataset(
    *,
    symbols: Sequence[str],
    output_root: Path,
    duration_seconds: int,
    project_root: Path,
    config_path: Path,
) -> Path:
    recorder = RawEventRecorder(
        output_root,
        symbols,
        code_revision=_git_revision(project_root),
        config_revision=_file_revision(config_path),
    )
    return asyncio.run(GateMarketCapture(symbols, recorder).run(duration_seconds))


def _subscription_requests(symbols: Sequence[str]) -> tuple[dict[str, object], ...]:
    now = int(time.time())
    symbol_list = list(symbols)
    requests = build_gate_ws_subscriptions(
        symbol_list,
        unix_time=now,
        book_frequency=BOOK_FREQUENCY,
        book_level=BOOK_LEVELS,
    )
    return tuple(requests) + (
        {
            "time": now,
            "channel": "futures.tickers",
            "event": "subscribe",
            "payload": symbol_list,
        },
    )


def _fetch_book_snapshot(symbol: str) -> tuple[dict[str, object], int]:
    query = urlencode({"contract": symbol, "limit": BOOK_LEVELS, "with_id": "true"})
    request = Request(
        f"{GATE_FUTURES_REST_URL}?{query}",
        headers={"User-Agent": "Autotrade-7-Paper-Capture/1", **GATE_SIZE_DECIMAL_HEADER},
    )
    with urlopen(request, timeout=10) as response:
        payload = response.read(MAX_GATE_WS_MESSAGE_BYTES + 1)
    received_ns = time.time_ns()
    if len(payload) > MAX_GATE_WS_MESSAGE_BYTES:
        raise DatasetError("Gate snapshot exceeded size limit")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise DatasetError("Gate snapshot is not an object")
    return parsed, received_ns


def _read_manifest(dataset: Path) -> dict[str, object]:
    try:
        manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError("dataset manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise DatasetError("dataset manifest schema is unsupported")
    return manifest


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as file:
        file.write(_canonical_json(value))
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _positive_int(value: object, field: str) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed <= 0:
        raise DatasetError(f"{field} must be a positive integer")
    return parsed


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp_ms(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 < parsed < float("inf"):
        return None
    return int(parsed * 1000) if parsed < 1_000_000_000_000 else int(parsed)


def _git_revision(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        shell=False,
    )
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "UNBORN"


def _file_revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
