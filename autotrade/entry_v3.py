from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from autotrade.capture import (
    DatasetError,
    GateMarketCapture,
    RawEventRecorder,
    iter_replay_events,
)
from autotrade.config import DEFAULT_CONFIG_PATH, EntryV3Settings, Settings
from autotrade.integrity import LedgerSummary, reconcile_checkpoint, scan_ledger
from autotrade.market_data import GateLocalOrderBook, GateMarketDataError, normalize_gate_ws_message

BASELINE_STRATEGY_VERSION = "BASELINE_REST_MOMENTUM_V2"
ENTRY_V3_STRATEGY_VERSION = "MICROSTRUCTURE_ENTRY_V3"
FEATURE_SCHEMA_VERSION = 1
EDGE_MODEL_VERSION = "RULE_BASED_V1"
REPLAY_EXECUTION_MODEL_VERSION = "CONSERVATIVE_TAKER_REPLAY_V1"
LIVE_EXECUTION_MODEL_VERSION = "NAUTILUS_PAPER_SHADOW_NO_ORDERS_V1"
TAKER_FEE_BPS = Decimal("5")

Level = tuple[Decimal, Decimal]


class Regime(StrEnum):
    UNDEFINED = "UNDEFINED"
    CHOP = "CHOP"
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    IMPULSE_UP = "IMPULSE_UP"
    IMPULSE_DOWN = "IMPULSE_DOWN"
    PULLBACK_UPTREND = "PULLBACK_UPTREND"
    PULLBACK_DOWNTREND = "PULLBACK_DOWNTREND"
    EXHAUSTION_UP = "EXHAUSTION_UP"
    EXHAUSTION_DOWN = "EXHAUSTION_DOWN"


class EntryState(StrEnum):
    IDLE = "IDLE"
    IMPULSE_DETECTED = "IMPULSE_DETECTED"
    WAITING_FOR_PULLBACK = "WAITING_FOR_PULLBACK"
    PULLBACK_ACTIVE = "PULLBACK_ACTIVE"
    WAITING_FOR_REACCELERATION = "WAITING_FOR_REACCELERATION"
    ENTRY_ELIGIBLE = "ENTRY_ELIGIBLE"
    IN_POSITION = "IN_POSITION"
    COOLDOWN = "COOLDOWN"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    dataset_or_stream_id: str
    symbol: str | None
    exchange_timestamp_ms: int | None
    local_receive_timestamp_ns: int
    sequence: int
    event_type: str
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    l2_bids: tuple[Level, ...] = ()
    l2_asks: tuple[Level, ...] = ()
    trade_price: Decimal | None = None
    trade_size: Decimal | None = None
    trade_side: str | None = None
    trade_side_source: str = "UNKNOWN"
    tick_size: Decimal | None = None
    quantity_increment: Decimal | None = None
    contract_metadata: dict[str, object] | None = None
    book_sequence: int | None = None
    book_valid: bool = False
    connection_id: int | None = None
    data_quality_reason: str | None = None

    @property
    def event_time_ms(self) -> int:
        return self.local_receive_timestamp_ns // 1_000_000

    @property
    def mid_price(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return self.trade_price
        return (self.best_bid + self.best_ask) / 2

    @property
    def observed_latency_ms(self) -> Decimal | None:
        if self.exchange_timestamp_ms is None:
            return None
        latency = Decimal(self.event_time_ms - self.exchange_timestamp_ms)
        return latency if latency >= 0 else None


class MarketEventNormalizer:
    def __init__(
        self,
        stream_id: str,
        *,
        book_depth: int = 5,
        instruments: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.stream_id = stream_id
        self.book_depth = book_depth
        self.instruments = instruments or {}
        self.books: dict[str, GateLocalOrderBook] = {}
        self.pending: dict[str, deque[dict[str, object]]] = {}
        self.last_receive_ns = 0
        self.counters: Counter[str] = Counter()

    def normalize(self, record: dict[str, object]) -> list[MarketEvent]:
        received_ns = _positive_int(record.get("received_ts_ns"), "received_ts_ns")
        local_sequence = _positive_int(record.get("local_sequence"), "local_sequence")
        if received_ns < self.last_receive_ns:
            raise DatasetError("capture local receive timestamps are not ordered")
        self.last_receive_ns = received_ns
        channel = str(record.get("channel", ""))
        event = str(record.get("event", ""))
        if channel == "capture.connection":
            for book in self.books.values():
                book.clear()
            self.pending.clear()
            self.counters["reconnects"] += int(event == "connected" and local_sequence > 1)
            return [
                self._event(
                    record,
                    0,
                    event_type="CONNECTION",
                    symbol=None,
                    book_valid=False,
                    data_quality_reason=event.upper(),
                )
            ]
        if channel == "futures.order_book_snapshot":
            return [self._snapshot(record)]
        if event != "update" or not channel.startswith("futures."):
            return []
        payload = record.get("payload")
        try:
            normalized = normalize_gate_ws_message(
                _canonical_json(payload),
                local_receive_utc=_utc_from_ns(received_ns),
            )
        except GateMarketDataError as exc:
            raise DatasetError(str(exc)) from exc
        output: list[MarketEvent] = []
        for index, item in enumerate(normalized):
            kind = str(item.get("type", ""))
            if kind == "trade":
                output.append(self._trade(record, index, item))
            elif kind == "book_ticker":
                output.append(self._ticker(record, index, item))
            elif kind == "order_book_delta":
                output.append(self._book_delta(record, index, item))
        return output

    def _snapshot(self, record: dict[str, object]) -> MarketEvent:
        symbol = str(record.get("symbol", ""))
        if not symbol:
            raise DatasetError("order-book snapshot has no symbol")
        book = self.books.setdefault(symbol, GateLocalOrderBook(symbol))
        try:
            book.apply_snapshot(record.get("payload"))
            buffered = self.pending.pop(symbol, deque())
            while buffered:
                outcome = book.apply_delta(buffered.popleft())
                if outcome == "GAP":
                    self.counters["sequence_gaps"] += 1
                    return self._event(
                        record,
                        0,
                        event_type="BOOK_GAP",
                        symbol=symbol,
                        book_valid=False,
                        data_quality_reason="SEQUENCE_GAP_DURING_RESNAPSHOT",
                    )
            self.counters["resnapshots"] += 1
            return self._book_event(record, 0, symbol, "BOOK_SNAPSHOT", book)
        except GateMarketDataError as exc:
            book.clear()
            raise DatasetError(str(exc)) from exc

    def _book_delta(
        self,
        record: dict[str, object],
        index: int,
        item: dict[str, object],
    ) -> MarketEvent:
        symbol = str(item.get("contract", ""))
        book = self.books.setdefault(symbol, GateLocalOrderBook(symbol))
        if not book.synchronized:
            queue = self.pending.setdefault(symbol, deque(maxlen=10_000))
            if len(queue) == queue.maxlen:
                raise DatasetError("order-book synchronization buffer exhausted")
            queue.append(item)
            return self._event(
                record,
                index,
                event_type="BOOK_UNSYNCHRONIZED",
                symbol=symbol,
                exchange_timestamp_ms=_optional_int(item.get("exchange_time_ms")),
                book_sequence=_optional_int(item.get("last_update_id")),
                book_valid=False,
                data_quality_reason="WAITING_FOR_SNAPSHOT",
            )
        try:
            outcome = book.apply_delta(item)
        except GateMarketDataError as exc:
            book.clear()
            raise DatasetError(str(exc)) from exc
        if outcome == "GAP":
            self.counters["sequence_gaps"] += 1
            self.pending[symbol] = deque((item,), maxlen=10_000)
            return self._event(
                record,
                index,
                event_type="BOOK_GAP",
                symbol=symbol,
                exchange_timestamp_ms=_optional_int(item.get("exchange_time_ms")),
                book_sequence=_optional_int(item.get("last_update_id")),
                book_valid=False,
                data_quality_reason="SEQUENCE_GAP",
            )
        if outcome == "DUPLICATE":
            self.counters["duplicates"] += 1
        return self._book_event(record, index, symbol, f"BOOK_{outcome}", book)

    def _book_event(
        self,
        record: dict[str, object],
        index: int,
        symbol: str,
        event_type: str,
        book: GateLocalOrderBook,
    ) -> MarketEvent:
        bids, asks = book.levels(self.book_depth)
        metadata = self.instruments.get(symbol, {})
        return self._event(
            record,
            index,
            event_type=event_type,
            symbol=symbol,
            exchange_timestamp_ms=_optional_int(record.get("exchange_ts_ms")),
            best_bid=bids[0][0],
            best_ask=asks[0][0],
            bid_size=bids[0][1],
            ask_size=asks[0][1],
            l2_bids=bids,
            l2_asks=asks,
            tick_size=_optional_decimal(metadata.get("tick_size")),
            quantity_increment=_optional_decimal(metadata.get("quantity_increment")),
            contract_metadata=metadata or None,
            book_sequence=book.update_id,
            book_valid=True,
        )

    def _trade(
        self,
        record: dict[str, object],
        index: int,
        item: dict[str, object],
    ) -> MarketEvent:
        return self._event(
            record,
            index,
            event_type="TRADE",
            symbol=str(item.get("contract", "")),
            exchange_timestamp_ms=_optional_int(item.get("exchange_time_ms")),
            trade_price=_optional_decimal(item.get("price")),
            trade_size=abs(_optional_decimal(item.get("size")) or Decimal(0)),
            trade_side=str(item.get("side")) if item.get("side") is not None else None,
            trade_side_source="EXCHANGE",
            book_valid=False,
        )

    def _ticker(
        self,
        record: dict[str, object],
        index: int,
        item: dict[str, object],
    ) -> MarketEvent:
        symbol = str(item.get("contract", ""))
        book = self.books.get(symbol)
        return self._event(
            record,
            index,
            event_type="BOOK_TICKER",
            symbol=symbol,
            exchange_timestamp_ms=_optional_int(item.get("exchange_time_ms")),
            best_bid=_optional_decimal(item.get("bid")),
            best_ask=_optional_decimal(item.get("ask")),
            bid_size=_optional_decimal(item.get("bid_size")),
            ask_size=_optional_decimal(item.get("ask_size")),
            book_sequence=_optional_int(item.get("update_id")),
            book_valid=bool(book and book.synchronized),
            data_quality_reason=None if book and book.synchronized else "L2_NOT_SYNCHRONIZED",
        )

    def _event(
        self,
        record: dict[str, object],
        index: int,
        *,
        event_type: str,
        symbol: str | None,
        **values: Any,
    ) -> MarketEvent:
        local_sequence = _positive_int(record.get("local_sequence"), "local_sequence")
        raw_id = str(record.get("event_hash") or f"{self.stream_id}:{local_sequence}")
        event_id = hashlib.sha256(f"{raw_id}:{index}:{event_type}".encode()).hexdigest()
        return MarketEvent(
            event_id=event_id,
            dataset_or_stream_id=str(record.get("dataset_id") or self.stream_id),
            symbol=symbol,
            exchange_timestamp_ms=_optional_int(
                values.pop("exchange_timestamp_ms", record.get("exchange_ts_ms"))
            ),
            local_receive_timestamp_ns=_positive_int(
                record.get("received_ts_ns"), "received_ts_ns"
            ),
            sequence=local_sequence * 1000 + index,
            event_type=event_type,
            connection_id=_optional_int(record.get("connection_id")),
            **values,
        )


@dataclass(frozen=True)
class FeatureSnapshot:
    symbol: str
    timestamp_ms: int
    sequence: int
    sample_count: int
    data_valid: bool
    data_quality_reason: str | None
    mid_price: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    spread_bps: Decimal | None
    spread_percentile: Decimal | None
    spread_trend: str
    l1_imbalance: Decimal | None
    multi_level_imbalance: Decimal | None
    microprice: Decimal | None
    microprice_delta_bps: Decimal | None
    bid_depth: Decimal | None
    ask_depth: Decimal | None
    bid_depletion: Decimal | None
    ask_depletion: Decimal | None
    bid_rebuild: Decimal | None
    ask_rebuild: Decimal | None
    aggressive_buy_volume: Decimal
    aggressive_sell_volume: Decimal
    trade_flow_imbalance: Decimal
    trade_arrival_rate: Decimal
    trade_arrival_acceleration: Decimal
    short_vwap: Decimal | None
    distance_from_vwap_bps: Decimal | None
    vwap_slope_bps: Decimal | None
    price_slope_bps: Decimal
    realized_volatility_bps: Decimal
    reversal_frequency: Decimal
    observed_latency_ms: Decimal | None
    book_sequence: int | None
    window_start_ms: int


class FeatureEngine:
    def __init__(self, settings: EntryV3Settings) -> None:
        self.settings = settings
        limit = settings.feature_window_events
        self.prices: deque[tuple[int, Decimal]] = deque(maxlen=limit)
        self.trades: deque[tuple[int, Decimal, Decimal, Decimal]] = deque(maxlen=limit)
        self.spreads: deque[Decimal] = deque(maxlen=limit)
        self.last_book_ms: int | None = None
        self.last_bid_depth: Decimal | None = None
        self.last_ask_depth: Decimal | None = None
        self.last_snapshot: FeatureSnapshot | None = None
        self.processing_samples_ms: deque[float] = deque(maxlen=limit)

    def reset_book(self, reason: str) -> None:
        self.last_book_ms = None
        self.last_bid_depth = None
        self.last_ask_depth = None
        if self.last_snapshot is not None:
            self.last_snapshot = FeatureSnapshot(
                **{
                    **asdict(self.last_snapshot),
                    "data_valid": False,
                    "data_quality_reason": reason,
                }
            )

    def update(self, event: MarketEvent) -> FeatureSnapshot | None:
        if event.symbol is None:
            return None
        started = time.perf_counter_ns()
        now = event.event_time_ms
        cutoff = now - self.settings.feature_window_ms
        if event.event_type in {"BOOK_GAP", "BOOK_UNSYNCHRONIZED"}:
            self.reset_book(event.data_quality_reason or event.event_type)
        if event.trade_price is not None and event.trade_size is not None:
            buy = event.trade_size if event.trade_side == "BUY" else Decimal(0)
            sell = event.trade_size if event.trade_side == "SELL" else Decimal(0)
            self.trades.append((now, event.trade_price, buy, sell))
        if event.book_valid and event.mid_price is not None and event.l2_bids and event.l2_asks:
            self.last_book_ms = now
            self.prices.append((now, event.mid_price))
            assert event.best_bid is not None and event.best_ask is not None
            spread = _bps(event.best_ask - event.best_bid, event.mid_price)
            self.spreads.append(spread)
        _trim(self.prices, cutoff)
        _trim(self.trades, cutoff)
        snapshot = self._snapshot(event, cutoff)
        self.last_snapshot = snapshot
        self.processing_samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        return snapshot

    def _snapshot(self, event: MarketEvent, cutoff: int) -> FeatureSnapshot:
        previous = self.last_snapshot
        bid_depth = _notional_depth(event.l2_bids) if event.l2_bids else None
        ask_depth = _notional_depth(event.l2_asks) if event.l2_asks else None
        bid_depletion, bid_rebuild = _depth_change(self.last_bid_depth, bid_depth)
        ask_depletion, ask_rebuild = _depth_change(self.last_ask_depth, ask_depth)
        if bid_depth is not None:
            self.last_bid_depth = bid_depth
        if ask_depth is not None:
            self.last_ask_depth = ask_depth
        bid_size = event.bid_size
        ask_size = event.ask_size
        top_total = (bid_size or Decimal(0)) + (ask_size or Decimal(0))
        l1 = bid_size / top_total if bid_size is not None and top_total else None
        depth_total = (bid_depth or Decimal(0)) + (ask_depth or Decimal(0))
        multi = (
            ((bid_depth or Decimal(0)) - (ask_depth or Decimal(0))) / depth_total
            if bid_depth is not None and ask_depth is not None and depth_total
            else None
        )
        microprice = None
        if (
            event.best_bid is not None
            and event.best_ask is not None
            and bid_size is not None
            and ask_size is not None
            and top_total
        ):
            microprice = (event.best_ask * bid_size + event.best_bid * ask_size) / top_total
        mid = event.mid_price or (previous.mid_price if previous else None)
        spread = (
            _bps(event.best_ask - event.best_bid, mid)
            if event.best_bid is not None and event.best_ask is not None and mid
            else (previous.spread_bps if previous else None)
        )
        spread_percentile = None
        if spread is not None and self.spreads:
            spread_percentile = Decimal(sum(value <= spread for value in self.spreads)) / Decimal(
                len(self.spreads)
            )
        spread_trend = "UNKNOWN"
        if spread is not None and len(self.spreads) >= 4:
            prior = sum(tuple(self.spreads)[:-1], Decimal(0)) / Decimal(len(self.spreads) - 1)
            spread_trend = (
                "EXPANDING"
                if spread > prior * Decimal("1.10")
                else "CONTRACTING"
                if spread < prior * Decimal("0.90")
                else "STABLE"
            )
        buy_volume = sum((item[2] for item in self.trades), Decimal(0))
        sell_volume = sum((item[3] for item in self.trades), Decimal(0))
        total_volume = buy_volume + sell_volume
        flow = (buy_volume - sell_volume) / total_volume if total_volume else Decimal(0)
        duration_seconds = Decimal(max(1, self.settings.feature_window_ms)) / 1000
        arrival = Decimal(len(self.trades)) / duration_seconds
        midpoint = cutoff + self.settings.feature_window_ms // 2
        first_count = sum(item[0] < midpoint for item in self.trades)
        second_count = len(self.trades) - first_count
        acceleration = (
            Decimal(second_count) / Decimal(first_count)
            if first_count
            else Decimal(1 if second_count else 0)
        )
        vwap = (
            sum((price * (buy + sell) for _, price, buy, sell in self.trades), Decimal(0))
            / total_volume
            if total_volume
            else None
        )
        first_trades = [item for item in self.trades if item[0] < midpoint]
        second_trades = [item for item in self.trades if item[0] >= midpoint]
        first_vwap = _vwap(first_trades)
        second_vwap = _vwap(second_trades)
        vwap_slope = (
            _bps(second_vwap - first_vwap, first_vwap) if first_vwap and second_vwap else None
        )
        impulse_cutoff = event.event_time_ms - self.settings.impulse_window_ms
        impulse_prices = [item for item in self.prices if item[0] >= impulse_cutoff]
        price_slope = (
            _bps(impulse_prices[-1][1] - impulse_prices[0][1], impulse_prices[0][1])
            if len(impulse_prices) >= 2
            else Decimal(0)
        )
        returns = [
            _bps(current[1] - prior[1], prior[1])
            for prior, current in zip(self.prices, tuple(self.prices)[1:], strict=False)
            if prior[1]
        ]
        volatility = (
            Decimal(str(math.sqrt(sum(float(value * value) for value in returns) / len(returns))))
            if returns
            else Decimal(0)
        )
        signs = [1 if value > 0 else -1 for value in returns if value]
        reversals = sum(a != b for a, b in zip(signs, signs[1:], strict=False))
        reversal_frequency = (
            Decimal(reversals) / Decimal(len(signs) - 1) if len(signs) > 1 else Decimal(0)
        )
        book_fresh = self.last_book_ms is not None and (
            event.event_time_ms - self.last_book_ms <= self.settings.impulse_window_ms
        )
        latency = event.observed_latency_ms
        return FeatureSnapshot(
            symbol=event.symbol or "",
            timestamp_ms=event.event_time_ms,
            sequence=event.sequence,
            sample_count=len(self.prices),
            data_valid=book_fresh,
            data_quality_reason=None
            if book_fresh
            else event.data_quality_reason or "STALE_OR_MISSING_BOOK",
            mid_price=mid,
            best_bid=event.best_bid or (previous.best_bid if previous else None),
            best_ask=event.best_ask or (previous.best_ask if previous else None),
            spread_bps=spread,
            spread_percentile=spread_percentile,
            spread_trend=spread_trend,
            l1_imbalance=l1 if l1 is not None else (previous.l1_imbalance if previous else None),
            multi_level_imbalance=multi
            if multi is not None
            else (previous.multi_level_imbalance if previous else None),
            microprice=microprice or (previous.microprice if previous else None),
            microprice_delta_bps=_bps(microprice - mid, mid) if microprice and mid else None,
            bid_depth=bid_depth
            if bid_depth is not None
            else (previous.bid_depth if previous else None),
            ask_depth=ask_depth
            if ask_depth is not None
            else (previous.ask_depth if previous else None),
            bid_depletion=bid_depletion,
            ask_depletion=ask_depletion,
            bid_rebuild=bid_rebuild,
            ask_rebuild=ask_rebuild,
            aggressive_buy_volume=buy_volume,
            aggressive_sell_volume=sell_volume,
            trade_flow_imbalance=flow,
            trade_arrival_rate=arrival,
            trade_arrival_acceleration=acceleration,
            short_vwap=vwap,
            distance_from_vwap_bps=_bps(mid - vwap, vwap) if mid and vwap else None,
            vwap_slope_bps=vwap_slope,
            price_slope_bps=price_slope,
            realized_volatility_bps=volatility,
            reversal_frequency=reversal_frequency,
            observed_latency_ms=latency,
            book_sequence=event.book_sequence
            if event.book_sequence is not None
            else (previous.book_sequence if previous else None),
            window_start_ms=cutoff,
        )


def classify_regime(
    features: FeatureSnapshot,
    settings: EntryV3Settings,
    state: EntryState = EntryState.IDLE,
) -> tuple[Regime, Decimal]:
    if not features.data_valid or features.sample_count < settings.minimum_events:
        return Regime.UNDEFINED, Decimal(0)
    direction = 1 if features.price_slope_bps > 0 else -1 if features.price_slope_bps < 0 else 0
    flow_aligned = features.trade_flow_imbalance * direction >= settings.flow_confirmation
    book_aligned = (
        features.multi_level_imbalance is not None
        and features.multi_level_imbalance * direction >= settings.book_confirmation
    )
    impulse = abs(features.price_slope_bps) >= settings.impulse_min_bps
    extension = abs(features.distance_from_vwap_bps or Decimal(0))
    exhausted = (
        impulse
        and extension >= settings.vwap_extension_bps
        and (
            not flow_aligned
            or features.spread_trend == "EXPANDING"
            or (
                features.microprice_delta_bps is not None
                and features.microprice_delta_bps * direction <= 0
            )
        )
    )
    if exhausted:
        regime = Regime.EXHAUSTION_UP if direction > 0 else Regime.EXHAUSTION_DOWN
        return regime, _agreement(
            impulse, extension >= settings.vwap_extension_bps, not flow_aligned
        )
    if state in {
        EntryState.PULLBACK_ACTIVE,
        EntryState.WAITING_FOR_REACCELERATION,
    }:
        regime = Regime.PULLBACK_UPTREND if direction >= 0 else Regime.PULLBACK_DOWNTREND
        return regime, _agreement(True, flow_aligned, book_aligned)
    if impulse and flow_aligned and book_aligned:
        regime = Regime.IMPULSE_UP if direction > 0 else Regime.IMPULSE_DOWN
        return regime, _agreement(impulse, flow_aligned, book_aligned)
    if (
        features.reversal_frequency >= Decimal("0.50")
        and abs(features.price_slope_bps) < settings.impulse_min_bps
    ):
        return Regime.CHOP, _agreement(True, features.realized_volatility_bps > 0)
    trend_hurdle = settings.impulse_min_bps / 2
    if features.price_slope_bps >= trend_hurdle:
        return Regime.TREND_UP, _agreement(True, flow_aligned, book_aligned)
    if features.price_slope_bps <= -trend_hurdle:
        return Regime.TREND_DOWN, _agreement(True, flow_aligned, book_aligned)
    return Regime.CHOP, _agreement(True, features.reversal_frequency > Decimal("0.20"))


@dataclass
class SymbolEntryState:
    state: EntryState = EntryState.IDLE
    regime: Regime = Regime.UNDEFINED
    regime_id: str | None = None
    impulse_id: str | None = None
    direction: str | None = None
    impulse_start_ms: int | None = None
    impulse_start_price: Decimal | None = None
    impulse_high: Decimal | None = None
    impulse_low: Decimal | None = None
    pullback_extreme: Decimal | None = None
    pullback_start_ms: int | None = None
    last_failed_direction: str | None = None
    last_failed_ms: int | None = None
    last_impulse_id: str | None = None
    timeline: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=64))


class EntryV3Strategy:
    def __init__(self, settings: Settings, experiment_id: str) -> None:
        self.settings = settings
        self.entry = settings.entry_v3
        self.experiment_id = experiment_id
        self.features: dict[str, FeatureEngine] = {}
        self.states: dict[str, SymbolEntryState] = {}
        self.counters: Counter[str] = Counter()
        self.events: list[dict[str, object]] = []
        self.processing_ms: deque[float] = deque(maxlen=self.entry.feature_window_events)

    def process(self, event: MarketEvent) -> list[dict[str, object]]:
        self.counters["market_events_received"] += 1
        if event.symbol is None:
            return []
        started = time.perf_counter_ns()
        engine = self.features.setdefault(event.symbol, FeatureEngine(self.entry))
        features = engine.update(event)
        if features is None:
            return []
        output = self.evaluate_features(features, event)
        self.events.extend(output)
        self.processing_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        return output

    def evaluate_features(
        self,
        features: FeatureSnapshot,
        market_event: MarketEvent | None = None,
    ) -> list[dict[str, object]]:
        state = self.states.setdefault(features.symbol, SymbolEntryState())
        output: list[dict[str, object]] = []
        regime, confidence = classify_regime(features, self.entry, state.state)
        if regime != state.regime:
            state.regime = regime
            state.regime_id = _stable_id(
                self.experiment_id,
                features.symbol,
                str(features.sequence),
                regime.value,
            )
            self.counters["regime_transitions"] += 1
            output.append(
                self._transition(state, features, state.state, state.state, "REGIME_CHANGE")
            )
        if not features.data_valid or features.sample_count < self.entry.minimum_events:
            if state.state != EntryState.QUARANTINED:
                output.append(
                    self._transition(
                        state,
                        features,
                        state.state,
                        EntryState.QUARANTINED,
                        features.data_quality_reason or "DATA_QUALITY",
                    )
                )
                state.state = EntryState.QUARANTINED
            self.counters["entries_rejected_data_quality"] += 1
            return output
        if state.state == EntryState.QUARANTINED:
            output.append(
                self._transition(
                    state,
                    features,
                    EntryState.QUARANTINED,
                    EntryState.IDLE,
                    "BOOK_RESYNCHRONIZED",
                )
            )
            state.state = EntryState.IDLE
        if features.mid_price is None:
            return output
        if state.state in {EntryState.ENTRY_ELIGIBLE, EntryState.IN_POSITION}:
            return output
        direction = "LONG" if features.price_slope_bps > 0 else "SHORT"
        signed = Decimal(1 if direction == "LONG" else -1)
        if state.state == EntryState.COOLDOWN:
            blocked = (
                state.last_failed_ms is not None
                and features.timestamp_ms - state.last_failed_ms < self.entry.whipsaw_block_ms
            )
            if blocked:
                if direction != state.last_failed_direction:
                    output.append(
                        self._candidate(
                            state,
                            features,
                            direction,
                            confidence,
                            "NONE",
                            "REJECTED",
                            "WHIPSAW_DIRECTION_FLIP",
                        )
                    )
                    self.counters["entries_rejected_whipsaw"] += 1
                return output
            state.state = EntryState.IDLE
        if regime == Regime.CHOP:
            if abs(features.price_slope_bps) >= self.entry.impulse_min_bps / 2:
                output.append(
                    self._candidate(
                        state,
                        features,
                        direction,
                        confidence,
                        "NONE",
                        "REJECTED",
                        "CHOP_REGIME",
                    )
                )
                self.counters["entries_rejected_chop"] += 1
            return output
        if state.state == EntryState.IDLE:
            if not self._impulse_confirmed(features, signed):
                return output
            state.impulse_id = _stable_id(
                self.experiment_id,
                features.symbol,
                str(features.sequence),
                direction,
                "impulse",
            )
            if state.impulse_id == state.last_impulse_id:
                return output
            state.direction = direction
            state.impulse_start_ms = features.timestamp_ms
            state.impulse_start_price = features.mid_price
            state.impulse_high = features.mid_price
            state.impulse_low = features.mid_price
            state.pullback_extreme = None
            state.pullback_start_ms = None
            output.append(
                self._transition(
                    state,
                    features,
                    EntryState.IDLE,
                    EntryState.IMPULSE_DETECTED,
                    "DIRECTIONAL_IMPULSE_CONFIRMED",
                )
            )
            state.state = EntryState.IMPULSE_DETECTED
            self.counters["impulses_detected"] += 1
            output.append(
                self._candidate(
                    state,
                    features,
                    direction,
                    confidence,
                    "NONE",
                    "REJECTED",
                    "IMPULSE_OBSERVATION_REQUIRED",
                )
            )
            return output
        if state.state == EntryState.IMPULSE_DETECTED:
            output.append(
                self._transition(
                    state,
                    features,
                    EntryState.IMPULSE_DETECTED,
                    EntryState.WAITING_FOR_PULLBACK,
                    "RAW_IMPULSE_NEVER_ENTERS",
                )
            )
            state.state = EntryState.WAITING_FOR_PULLBACK
        self._update_extremes(state, features.mid_price)
        pullback_bps, pullback_ratio = self._pullback(state, features.mid_price)
        if pullback_ratio > self.entry.pullback_max_ratio:
            output.append(
                self._candidate(
                    state,
                    features,
                    direction,
                    confidence,
                    "PULLBACK",
                    "REJECTED",
                    "STRUCTURAL_REVERSAL",
                    pullback_bps,
                    pullback_ratio,
                )
            )
            self._fail_impulse(state, features)
            return output
        if state.state == EntryState.WAITING_FOR_PULLBACK:
            if pullback_ratio >= self.entry.pullback_min_ratio:
                state.pullback_start_ms = features.timestamp_ms
                state.pullback_extreme = features.mid_price
                output.append(
                    self._transition(
                        state,
                        features,
                        EntryState.WAITING_FOR_PULLBACK,
                        EntryState.PULLBACK_ACTIVE,
                        "HEALTHY_PULLBACK",
                    )
                )
                state.state = EntryState.PULLBACK_ACTIVE
                self.counters["pullbacks_detected"] += 1
                return output
            veto = self._exhaustion_veto(features, signed)
            if veto is not None:
                output.append(
                    self._candidate(
                        state,
                        features,
                        direction,
                        confidence,
                        "BREAKOUT_REACCELERATION",
                        "REJECTED",
                        veto,
                        pullback_bps,
                        pullback_ratio,
                    )
                )
                self.counters[
                    "entries_rejected_chase"
                    if veto == "CHASE_TOO_EXTENDED"
                    else "entries_rejected_exhaustion"
                ] += 1
                self._fail_impulse(state, features)
                return output
            if self._breakout_confirmed(features, signed):
                return output + self._edge_decision(
                    state,
                    features,
                    confidence,
                    "BREAKOUT_REACCELERATION",
                    pullback_bps,
                    pullback_ratio,
                )
            output.append(
                self._candidate(
                    state,
                    features,
                    direction,
                    confidence,
                    "NONE",
                    "REJECTED",
                    "WAITING_FOR_PULLBACK",
                    pullback_bps,
                    pullback_ratio,
                )
            )
            return output
        if state.state in {EntryState.PULLBACK_ACTIVE, EntryState.WAITING_FOR_REACCELERATION}:
            state.state = EntryState.WAITING_FOR_REACCELERATION
            state.pullback_extreme = (
                min(state.pullback_extreme or features.mid_price, features.mid_price)
                if state.direction == "LONG"
                else max(state.pullback_extreme or features.mid_price, features.mid_price)
            )
            rebound = (
                _bps(
                    features.mid_price - state.pullback_extreme,
                    state.pullback_extreme,
                )
                * signed
            )
            if rebound >= self.entry.reacceleration_bps and self._continuation_confirmed(
                features, signed
            ):
                return output + self._edge_decision(
                    state,
                    features,
                    confidence,
                    "MOMENTUM_PULLBACK",
                    pullback_bps,
                    pullback_ratio,
                )
            output.append(
                self._candidate(
                    state,
                    features,
                    direction,
                    confidence,
                    "MOMENTUM_PULLBACK",
                    "REJECTED",
                    "WAITING_FOR_REACCELERATION",
                    pullback_bps,
                    pullback_ratio,
                )
            )
        return output

    def mark_entered(self, symbol: str, candidate: dict[str, object]) -> None:
        state = self.states[symbol]
        state.state = EntryState.IN_POSITION
        state.timeline.append(
            {
                "timestamp_ms": candidate["timestamp_ms"],
                "event": "ENTRY",
                "reason": candidate["entry_type"],
            }
        )

    def mark_exited(self, symbol: str, timestamp_ms: int, reason: str) -> None:
        state = self.states[symbol]
        state.timeline.append({"timestamp_ms": timestamp_ms, "event": "EXIT", "reason": reason})
        state.last_impulse_id = state.impulse_id
        state.last_failed_direction = state.direction
        state.last_failed_ms = timestamp_ms
        state.state = EntryState.COOLDOWN

    def diagnostics(self) -> dict[str, object]:
        symbols: dict[str, object] = {}
        for symbol, state in sorted(self.states.items()):
            features = self.features.get(symbol)
            latest = features.last_snapshot if features else None
            symbols[symbol] = {
                "state": state.state.value,
                "regime": state.regime.value,
                "regime_id": state.regime_id,
                "impulse_id": state.impulse_id,
                "direction": state.direction,
                "latest_features": _serialize(asdict(latest)) if latest else None,
                "timeline": list(state.timeline),
            }
        processing = list(self.processing_ms)
        return {
            "strategy_version": ENTRY_V3_STRATEGY_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "edge_model_version": EDGE_MODEL_VERSION,
            "mode": "PAPER_SHADOW",
            "order_submission_enabled": False,
            "counters": dict(sorted(self.counters.items())),
            "strategy_event_processing_ms": {
                "samples": len(processing),
                "average": round(statistics.fmean(processing), 6) if processing else None,
                "maximum": round(max(processing), 6) if processing else None,
            },
            "symbols": symbols,
        }

    def _impulse_confirmed(self, features: FeatureSnapshot, signed: Decimal) -> bool:
        return (
            abs(features.price_slope_bps) >= self.entry.impulse_min_bps
            and features.price_slope_bps * signed > 0
            and features.trade_flow_imbalance * signed >= self.entry.flow_confirmation
            and (features.multi_level_imbalance or Decimal(0)) * signed
            >= self.entry.book_confirmation
            and (features.spread_bps or Decimal("Infinity")) <= self.entry.maximum_spread_bps
        )

    def _continuation_confirmed(self, features: FeatureSnapshot, signed: Decimal) -> bool:
        return (
            features.trade_flow_imbalance * signed >= self.entry.flow_confirmation
            and (features.multi_level_imbalance or Decimal(0)) * signed
            >= self.entry.book_confirmation
            and (features.microprice_delta_bps or Decimal(0)) * signed > 0
            and features.spread_trend != "EXPANDING"
        )

    def _breakout_confirmed(self, features: FeatureSnapshot, signed: Decimal) -> bool:
        depletion = features.ask_depletion if signed > 0 else features.bid_depletion
        return (
            features.trade_flow_imbalance * signed >= self.entry.breakout_flow
            and (features.multi_level_imbalance or Decimal(0)) * signed
            >= self.entry.book_confirmation
            and (features.microprice_delta_bps or Decimal(0)) * signed > 0
            and (depletion or Decimal(0)) > 0
            and features.trade_arrival_acceleration >= 1
            and features.spread_trend in {"CONTRACTING", "STABLE"}
        )

    def _exhaustion_veto(self, features: FeatureSnapshot, signed: Decimal) -> str | None:
        evidence: list[str] = []
        extension = (features.distance_from_vwap_bps or Decimal(0)) * signed
        if extension >= self.entry.vwap_extension_bps:
            evidence.append("CHASE_TOO_EXTENDED")
        if features.trade_flow_imbalance * signed < self.entry.flow_confirmation / 2:
            evidence.append("FLOW_DECELERATING")
        if (features.microprice_delta_bps or Decimal(0)) * signed <= 0:
            evidence.append("MICROPRICE_DIVERGENCE")
        if features.spread_trend == "EXPANDING":
            evidence.append("SPREAD_EXPANDING")
        rebuild = features.ask_rebuild if signed > 0 else features.bid_rebuild
        if (rebuild or Decimal(0)) >= Decimal("0.10"):
            evidence.append("ASK_LIQUIDITY_REBUILD" if signed > 0 else "BID_LIQUIDITY_REBUILD")
        return (
            evidence[0]
            if "CHASE_TOO_EXTENDED" in evidence and len(evidence) >= 2
            else (evidence[0] if len(evidence) >= 3 else None)
        )

    def _edge_decision(
        self,
        state: SymbolEntryState,
        features: FeatureSnapshot,
        confidence: Decimal,
        entry_type: str,
        pullback_bps: Decimal,
        pullback_ratio: Decimal,
    ) -> list[dict[str, object]]:
        impulse_size = self._impulse_size(state)
        remaining = max(Decimal(0), Decimal(1) - pullback_ratio)
        structure_discount = remaining if entry_type == "MOMENTUM_PULLBACK" else Decimal("0.50")
        gross = impulse_size * self.entry.continuation_fraction * structure_discount
        spread = features.spread_bps or Decimal(0)
        slippage = self.settings.strategy_slippage_bps * 2
        latency_ms = min(
            features.observed_latency_ms or Decimal(0),
            Decimal(self.entry.observed_latency_cap_ms),
        )
        latency_cost = (
            abs(features.price_slope_bps)
            * latency_ms
            / Decimal(max(1, self.entry.impulse_window_ms))
        )
        fee = TAKER_FEE_BPS * 2
        costs = fee + spread + slippage + latency_cost
        net = gross - costs
        decision = "ACCEPTED" if net >= self.entry.minimum_net_edge_bps else "REJECTED"
        reason = None if decision == "ACCEPTED" else "EXPECTED_NET_EDGE_BELOW_HURDLE"
        candidate = self._candidate(
            state,
            features,
            state.direction or "UNKNOWN",
            confidence,
            entry_type,
            decision,
            reason,
            pullback_bps,
            pullback_ratio,
            gross=gross,
            fee=fee,
            spread=spread,
            slippage=slippage,
            latency=latency_cost,
            net=net,
        )
        if decision == "ACCEPTED":
            state.state = EntryState.ENTRY_ELIGIBLE
            self.counters["entries_accepted"] += 1
            if entry_type == "BREAKOUT_REACCELERATION":
                self.counters["breakouts_detected"] += 1
        else:
            self.counters["entries_rejected_cost"] += 1
        return [candidate]

    def _candidate(
        self,
        state: SymbolEntryState,
        features: FeatureSnapshot,
        direction: str,
        confidence: Decimal,
        entry_type: str,
        decision: str,
        rejection_reason: str | None,
        pullback_bps: Decimal = Decimal(0),
        pullback_ratio: Decimal = Decimal(0),
        *,
        gross: Decimal = Decimal(0),
        fee: Decimal = TAKER_FEE_BPS * 2,
        spread: Decimal | None = None,
        slippage: Decimal | None = None,
        latency: Decimal = Decimal(0),
        net: Decimal | None = None,
    ) -> dict[str, object]:
        candidate_id = _stable_id(
            self.experiment_id,
            features.symbol,
            str(features.sequence),
            entry_type,
            direction,
            rejection_reason or "ACCEPTED",
        )
        self.counters["entry_candidates"] += 1
        impulse_duration = (
            features.timestamp_ms - state.impulse_start_ms
            if state.impulse_start_ms is not None
            else 0
        )
        spread_cost = spread if spread is not None else features.spread_bps
        slippage_cost = (
            slippage if slippage is not None else self.settings.strategy_slippage_bps * 2
        )
        total_cost = fee + (spread_cost or Decimal(0)) + slippage_cost + latency
        expected_net = net if net is not None else gross - total_cost
        record: dict[str, object] = {
            "event_type": "entry_candidate",
            "candidate_id": candidate_id,
            "run_id": None,
            "experiment_id": self.experiment_id,
            "strategy_id": ENTRY_V3_STRATEGY_VERSION,
            "entry_type": entry_type,
            "symbol": features.symbol,
            "timestamp_ms": features.timestamp_ms,
            "regime": state.regime.value,
            "regime_confidence": _number(confidence),
            "regime_id": state.regime_id,
            "direction": direction,
            "mid_price": _number(features.mid_price),
            "best_bid": _number(features.best_bid),
            "best_ask": _number(features.best_ask),
            "spread_bps": _number(features.spread_bps),
            "impulse_id": state.impulse_id,
            "impulse_return_bps": _number(self._impulse_size(state)),
            "impulse_duration_ms": impulse_duration,
            "pullback_bps": _number(pullback_bps),
            "pullback_ratio": _number(pullback_ratio),
            "microprice": _number(features.microprice),
            "microprice_delta_bps": _number(features.microprice_delta_bps),
            "book_imbalance_l1": _number(features.l1_imbalance),
            "book_imbalance_multi_level": _number(features.multi_level_imbalance),
            "aggressive_buy_volume": _number(features.aggressive_buy_volume),
            "aggressive_sell_volume": _number(features.aggressive_sell_volume),
            "trade_flow_imbalance": _number(features.trade_flow_imbalance),
            "trade_arrival_rate": _number(features.trade_arrival_rate),
            "short_realized_volatility": _number(features.realized_volatility_bps),
            "distance_from_short_vwap_bps": _number(features.distance_from_vwap_bps),
            "estimated_fee_bps": _number(fee),
            "estimated_spread_cost_bps": _number(spread_cost),
            "estimated_slippage_bps": _number(slippage_cost),
            "estimated_latency_cost_bps": _number(latency),
            "expected_gross_edge_bps": _number(gross),
            "expected_net_edge_bps": _number(expected_net),
            "edge_estimation": "DISCOUNTED_IMPULSE_WITH_CONFIRMED_STRUCTURE",
            "decision": decision,
            "rejection_reason": rejection_reason,
            "market_event_sequence_at_decision": features.sequence,
            "decision_timestamp": features.timestamp_ms,
            "book_snapshot_id": features.book_sequence,
            "trade_flow_window_start_ms": features.window_start_ms,
            "trade_flow_window_end_ms": features.timestamp_ms,
            "strategy_version": ENTRY_V3_STRATEGY_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "edge_model_version": EDGE_MODEL_VERSION,
            "execution_model_version": REPLAY_EXECUTION_MODEL_VERSION,
            "config_hash": _file_hash(DEFAULT_CONFIG_PATH),
            "git_commit": _git_commit(DEFAULT_CONFIG_PATH.parent.parent),
            "microstructure_quality": microstructure_quality(features),
        }
        state.timeline.append(
            {
                "timestamp_ms": features.timestamp_ms,
                "event": "ENTRY" if decision == "ACCEPTED" else "REJECTED",
                "reason": entry_type if decision == "ACCEPTED" else rejection_reason,
            }
        )
        return record

    def _transition(
        self,
        state: SymbolEntryState,
        features: FeatureSnapshot,
        previous: EntryState,
        current: EntryState,
        reason: str,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "event_type": "entry_state_transition",
            "symbol": features.symbol,
            "timestamp_ms": features.timestamp_ms,
            "market_event_sequence": features.sequence,
            "previous_state": previous.value,
            "state": current.value,
            "reason": reason,
            "impulse_id": state.impulse_id,
            "regime_id": state.regime_id,
        }
        state.timeline.append(
            {"timestamp_ms": features.timestamp_ms, "event": current.value, "reason": reason}
        )
        return record

    @staticmethod
    def _update_extremes(state: SymbolEntryState, price: Decimal) -> None:
        state.impulse_high = max(state.impulse_high or price, price)
        state.impulse_low = min(state.impulse_low or price, price)

    @staticmethod
    def _impulse_size(state: SymbolEntryState) -> Decimal:
        if not state.impulse_start_price or state.impulse_high is None or state.impulse_low is None:
            return Decimal(0)
        extreme = state.impulse_high if state.direction == "LONG" else state.impulse_low
        return abs(_bps(extreme - state.impulse_start_price, state.impulse_start_price))

    def _pullback(
        self,
        state: SymbolEntryState,
        price: Decimal,
    ) -> tuple[Decimal, Decimal]:
        impulse_size = self._impulse_size(state)
        if not impulse_size:
            return Decimal(0), Decimal(0)
        if state.direction == "LONG":
            pullback = max(Decimal(0), _bps((state.impulse_high or price) - price, price))
        else:
            pullback = max(Decimal(0), _bps(price - (state.impulse_low or price), price))
        return pullback, pullback / impulse_size

    @staticmethod
    def _fail_impulse(state: SymbolEntryState, features: FeatureSnapshot) -> None:
        state.last_failed_direction = state.direction
        state.last_failed_ms = features.timestamp_ms
        state.last_impulse_id = state.impulse_id
        state.state = EntryState.COOLDOWN


def microstructure_quality(features: FeatureSnapshot) -> dict[str, float]:
    spread = float(features.spread_bps or Decimal("100"))
    spread_quality = max(0.0, min(1.0, 1.0 - spread / 20.0))
    depth = float((features.bid_depth or Decimal(0)) + (features.ask_depth or Decimal(0)))
    depth_quality = min(1.0, math.log10(1.0 + depth) / 7.0)
    frequency_quality = min(1.0, float(features.trade_arrival_rate) / 20.0)
    volatility = float(features.realized_volatility_bps)
    volatility_quality = max(0.0, 1.0 - abs(volatility - 3.0) / 12.0)
    stability = 1.0 - min(1.0, float(features.reversal_frequency))
    score = (
        spread_quality * 0.30
        + depth_quality * 0.25
        + frequency_quality * 0.20
        + volatility_quality * 0.15
        + stability * 0.10
    )
    return {
        "score": round(score, 6),
        "spread_quality": round(spread_quality, 6),
        "depth_quality": round(depth_quality, 6),
        "trade_frequency_quality": round(frequency_quality, 6),
        "volatility_quality": round(volatility_quality, 6),
        "book_stability_quality": round(stability, 6),
    }


class BaselineRestMomentumV2:
    def __init__(self, settings: Settings, experiment_id: str) -> None:
        self.settings = settings
        self.experiment_id = experiment_id
        self.history: dict[str, deque[tuple[int, Decimal, Decimal, Decimal]]] = defaultdict(
            lambda: deque(maxlen=settings.strategy_regime_window + 1)
        )
        self.in_position: set[str] = set()
        self.cooldown_until: dict[str, int] = {}
        self.events: list[dict[str, object]] = []
        self.counters: Counter[str] = Counter()

    def process(self, event: MarketEvent) -> list[dict[str, object]]:
        self.counters["market_events_replayed"] += 1
        if (
            event.symbol is None
            or event.best_bid is None
            or event.best_ask is None
            or not event.book_valid
        ):
            return []
        symbol = event.symbol
        mid = (event.best_bid + event.best_ask) / 2
        history = self.history[symbol]
        history.append((event.event_time_ms, mid, event.best_bid, event.best_ask))
        if symbol in self.in_position or event.event_time_ms < self.cooldown_until.get(symbol, 0):
            return []
        if len(history) < self.settings.strategy_regime_window + 1:
            return []
        values = tuple(history)
        signal_start = values[-self.settings.strategy_window - 1][1]
        regime_start = values[0][1]
        move = _bps(mid - signal_start, signal_start)
        prior_regime = _bps(signal_start - regime_start, regime_start)
        regime_move = _bps(mid - regime_start, regime_start)
        spread = _bps(event.best_ask - event.best_bid, mid)
        costs = Decimal(10) + spread + Decimal(3)
        gross = abs(move)
        net = gross - costs
        confidence = min(
            Decimal(1),
            max(
                Decimal(0),
                net / max(self.settings.strategy_entry_threshold_bps, Decimal(1)),
            ),
        )
        hurdle = max(self.settings.strategy_entry_threshold_bps, costs)
        if abs(move) <= hurdle / 2:
            return []
        direction = "LONG" if move > 0 else "SHORT"
        signed = Decimal(1 if direction == "LONG" else -1)
        persistence = values[-self.settings.strategy_persistence_ticks - 1 :]
        persistence_ok = all(
            (current[1] - previous[1]) * signed > 0
            for previous, current in zip(persistence, persistence[1:], strict=False)
        )
        regime_ok = (
            prior_regime * signed >= self.settings.strategy_entry_threshold_bps / 2
            and regime_move * signed >= self.settings.strategy_entry_threshold_bps
        )
        reason = None
        if (
            abs(move) <= hurdle
            or net < self.settings.strategy_minimum_net_edge_bps
            or confidence < self.settings.strategy_minimum_confidence
        ):
            reason = "BASELINE_EDGE"
        elif not persistence_ok or not regime_ok:
            reason = "BASELINE_CONFIRMATION"
        record: dict[str, object] = {
            "event_type": "entry_candidate",
            "candidate_id": _stable_id(
                self.experiment_id,
                BASELINE_STRATEGY_VERSION,
                symbol,
                str(event.sequence),
            ),
            "experiment_id": self.experiment_id,
            "strategy_id": BASELINE_STRATEGY_VERSION,
            "strategy_version": BASELINE_STRATEGY_VERSION,
            "feature_schema_version": None,
            "edge_model_version": "V2_MOVEMENT_PROXY_FROZEN",
            "execution_model_version": REPLAY_EXECUTION_MODEL_VERSION,
            "entry_type": "REST_MOMENTUM",
            "symbol": symbol,
            "timestamp_ms": event.event_time_ms,
            "direction": direction,
            "regime": "V2_TREND_CONFIRMATION",
            "regime_id": None,
            "impulse_id": None,
            "mid_price": _number(mid),
            "best_bid": _number(event.best_bid),
            "best_ask": _number(event.best_ask),
            "spread_bps": _number(spread),
            "impulse_return_bps": _number(abs(move)),
            "expected_gross_edge_bps": _number(gross),
            "estimated_fee_bps": 10.0,
            "estimated_spread_cost_bps": _number(spread),
            "estimated_slippage_bps": 0.0,
            "estimated_latency_cost_bps": 3.0,
            "expected_net_edge_bps": _number(net),
            "decision": "REJECTED" if reason else "ACCEPTED",
            "rejection_reason": reason,
            "market_event_sequence_at_decision": event.sequence,
            "decision_timestamp": event.event_time_ms,
            "book_snapshot_id": event.book_sequence,
            "trade_flow_window_start_ms": None,
            "trade_flow_window_end_ms": None,
            "config_hash": _file_hash(DEFAULT_CONFIG_PATH),
            "git_commit": _git_commit(DEFAULT_CONFIG_PATH.parent.parent),
        }
        self.events.append(record)
        self.counters["entry_candidates"] += 1
        self.counters["entries_accepted" if reason is None else "entries_rejected"] += 1
        return [record]

    def mark_entered(self, symbol: str, candidate: dict[str, object]) -> None:
        self.in_position.add(symbol)

    def mark_exited(self, symbol: str, timestamp_ms: int, reason: str) -> None:
        self.in_position.discard(symbol)
        self.cooldown_until[symbol] = timestamp_ms + self.settings.strategy_cooldown_seconds * 1000


@dataclass
class SimPosition:
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    entry_mid: Decimal
    entry_fee: Decimal
    entry_spread_cost: Decimal
    entry_slippage_cost: Decimal
    entered_ms: int
    candidate: dict[str, object]
    mfe_bps: Decimal = Decimal(0)
    mae_bps: Decimal = Decimal(0)
    time_to_mfe_ms: int = 0
    time_to_mae_ms: int = 0
    first_excursion: str | None = None
    forward_prices: dict[str, float | None] = field(
        default_factory=lambda: {"1s": None, "3s": None, "5s": None, "10s": None, "30s": None}
    )


class ConservativeReplaySimulator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.position: SimPosition | None = None
        self.trades: list[dict[str, object]] = []
        self.last_quotes: dict[str, tuple[Decimal, Decimal, int]] = {}
        self.equity = settings.starting_balance_usdt
        self.peak_equity = self.equity
        self.max_drawdown = Decimal(0)

    def mark(self, event: MarketEvent) -> dict[str, object] | None:
        if event.symbol and event.best_bid is not None and event.best_ask is not None:
            self.last_quotes[event.symbol] = (event.best_bid, event.best_ask, event.event_time_ms)
        position = self.position
        if position is None or event.symbol != position.symbol:
            return None
        quote = self.last_quotes.get(position.symbol)
        if quote is None:
            return None
        bid, ask, timestamp_ms = quote
        mid = (bid + ask) / 2
        signed = Decimal(1 if position.side == "LONG" else -1)
        excursion = _bps(mid - position.entry_mid, position.entry_mid) * signed
        age = timestamp_ms - position.entered_ms
        if excursion > position.mfe_bps:
            position.mfe_bps = excursion
            position.time_to_mfe_ms = age
        if excursion < position.mae_bps:
            position.mae_bps = excursion
            position.time_to_mae_ms = age
        if position.first_excursion is None and excursion:
            position.first_excursion = "FAVORABLE" if excursion > 0 else "ADVERSE"
        for label, interval in (
            ("1s", 1000),
            ("3s", 3000),
            ("5s", 5000),
            ("10s", 10000),
            ("30s", 30000),
        ):
            if position.forward_prices[label] is None and age >= interval:
                position.forward_prices[label] = float(mid)
        reason = None
        if excursion <= -self.settings.strategy_stop_loss_bps:
            reason = "STOP_LOSS"
        elif excursion >= self.settings.strategy_take_profit_bps:
            reason = "TAKE_PROFIT"
        elif age >= self.settings.strategy_max_hold_seconds * 1000:
            reason = "TIME_EXIT"
        return self._close(timestamp_ms, bid, ask, reason) if reason else None

    def enter(self, event: MarketEvent, candidate: dict[str, object]) -> bool:
        if self.position is not None or event.symbol is None:
            return False
        quote = self.last_quotes.get(event.symbol)
        if quote is None:
            return False
        bid, ask, timestamp_ms = quote
        mid = (bid + ask) / 2
        side = str(candidate["direction"])
        slippage = self.settings.strategy_slippage_bps / Decimal(10_000)
        base = ask if side == "LONG" else bid
        fill = base * (Decimal(1) + slippage if side == "LONG" else Decimal(1) - slippage)
        quantity = self.settings.strategy_notional_usdt / fill
        entry_fee = fill * quantity * TAKER_FEE_BPS / Decimal(10_000)
        spread_cost = abs(base - mid) * quantity
        slippage_cost = abs(fill - base) * quantity
        self.position = SimPosition(
            symbol=event.symbol,
            side=side,
            quantity=quantity,
            entry_price=fill,
            entry_mid=mid,
            entry_fee=entry_fee,
            entry_spread_cost=spread_cost,
            entry_slippage_cost=slippage_cost,
            entered_ms=timestamp_ms,
            candidate=candidate,
        )
        return True

    def close_at_end(self) -> dict[str, object] | None:
        if self.position is None:
            return None
        quote = self.last_quotes.get(self.position.symbol)
        return self._close(quote[2], quote[0], quote[1], "DATASET_END") if quote else None

    def _close(
        self,
        timestamp_ms: int,
        bid: Decimal,
        ask: Decimal,
        reason: str,
    ) -> dict[str, object]:
        position = self.position
        if position is None:
            raise RuntimeError("replay position is unavailable")
        mid = (bid + ask) / 2
        slippage = self.settings.strategy_slippage_bps / Decimal(10_000)
        base = bid if position.side == "LONG" else ask
        fill = base * (Decimal(1) - slippage if position.side == "LONG" else Decimal(1) + slippage)
        signed = Decimal(1 if position.side == "LONG" else -1)
        gross = (fill - position.entry_price) * position.quantity * signed
        exit_fee = fill * position.quantity * TAKER_FEE_BPS / Decimal(10_000)
        fees = position.entry_fee + exit_fee
        net = gross - fees
        exit_spread = abs(base - mid) * position.quantity
        exit_slippage = abs(fill - base) * position.quantity
        stop_trigger = position.entry_mid * (
            Decimal(1) - self.settings.strategy_stop_loss_bps / Decimal(10_000) * signed
        )
        overshoot = (
            max(Decimal(0), _bps(stop_trigger - fill, stop_trigger) * signed)
            if reason == "STOP_LOSS"
            else Decimal(0)
        )
        trade = {
            "trade_id": _stable_id(str(position.candidate["candidate_id"]), "trade"),
            "candidate_id": position.candidate["candidate_id"],
            "strategy_version": position.candidate["strategy_version"],
            "feature_schema_version": position.candidate.get("feature_schema_version"),
            "edge_model_version": position.candidate.get("edge_model_version"),
            "execution_model_version": REPLAY_EXECUTION_MODEL_VERSION,
            "symbol": position.symbol,
            "side": position.side,
            "entry_type": position.candidate["entry_type"],
            "regime_at_entry": position.candidate["regime"],
            "entry_reason": position.candidate["entry_type"],
            "entry_candidate_id": position.candidate["candidate_id"],
            "signal_id": _stable_id(str(position.candidate["candidate_id"]), "signal"),
            "impulse_id": position.candidate.get("impulse_id"),
            "regime_id": position.candidate.get("regime_id"),
            "market_event_sequence_at_decision": position.candidate[
                "market_event_sequence_at_decision"
            ],
            "decision_timestamp": position.candidate["decision_timestamp"],
            "submit_timestamp": int(str(position.candidate["decision_timestamp"])) + 25,
            "fill_timestamp": int(str(position.candidate["decision_timestamp"])) + 50,
            "book_snapshot_id": position.candidate.get("book_snapshot_id"),
            "trade_flow_window_start_ms": position.candidate.get("trade_flow_window_start_ms"),
            "trade_flow_window_end_ms": position.candidate.get("trade_flow_window_end_ms"),
            "entry_price": _number(position.entry_price),
            "exit_price": _number(fill),
            "quantity": _number(position.quantity),
            "gross_pnl_usdt": _number(gross),
            "fees_usdt": _number(fees),
            "spread_cost_usdt": _number(position.entry_spread_cost + exit_spread),
            "slippage_cost_usdt": _number(position.entry_slippage_cost + exit_slippage),
            "net_pnl_usdt": _number(net),
            "mfe_bps": _number(position.mfe_bps),
            "mae_bps": _number(position.mae_bps),
            "time_to_mfe_ms": position.time_to_mfe_ms,
            "time_to_mae_ms": position.time_to_mae_ms,
            "mfe_before_mae": position.first_excursion == "FAVORABLE",
            "mae_before_mfe": position.first_excursion == "ADVERSE",
            "price_after_entry_1s": position.forward_prices["1s"],
            "price_after_entry_3s": position.forward_prices["3s"],
            "price_after_entry_5s": position.forward_prices["5s"],
            "price_after_entry_10s": position.forward_prices["10s"],
            "price_after_entry_30s": position.forward_prices["30s"],
            "stop_trigger_price": _number(stop_trigger) if reason == "STOP_LOSS" else None,
            "actual_modeled_fill_price": _number(fill),
            "stop_overshoot_bps": _number(overshoot),
            "stop_detection_latency_ms": max(0, timestamp_ms - position.entered_ms)
            if reason == "STOP_LOSS"
            else None,
            "exit_reason": reason,
            "closed_at_ms": timestamp_ms,
            "expected_net_edge_bps": position.candidate.get("expected_net_edge_bps"),
            "config_hash": position.candidate.get("config_hash"),
            "git_commit": position.candidate.get("git_commit"),
        }
        self.equity += net
        self.peak_equity = max(self.peak_equity, self.equity)
        if self.peak_equity:
            self.max_drawdown = max(
                self.max_drawdown,
                (self.peak_equity - self.equity) / self.peak_equity * 100,
            )
        self.trades.append(trade)
        self.position = None
        return trade


def iter_market_events(dataset: Path, settings: Settings) -> Iterator[MarketEvent]:
    manifest = _read_json(dataset / "manifest.json")
    stream_id = str(manifest.get("dataset_id", dataset.name))
    normalizer = MarketEventNormalizer(stream_id, book_depth=settings.entry_v3.book_depth)
    for record in iter_replay_events(dataset):
        yield from normalizer.normalize(record)


def run_replay_comparison(dataset: Path, settings: Settings) -> dict[str, object]:
    manifest = _read_json(dataset / "manifest.json")
    dataset_hash = _file_hash(dataset / "events.jsonl")
    config_hash = _file_hash(DEFAULT_CONFIG_PATH)
    git_commit = _git_commit(DEFAULT_CONFIG_PATH.parent.parent)
    code_hash = _code_hash(DEFAULT_CONFIG_PATH.parent.parent)
    identity = {
        "dataset_id": manifest.get("dataset_id"),
        "dataset_hash": dataset_hash,
        "git_commit": git_commit,
        "working_tree_code_hash": code_hash,
        "config_hash": config_hash,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "execution_model_version": REPLAY_EXECUTION_MODEL_VERSION,
        "random_seed": None,
    }
    experiment_id = hashlib.sha256(_canonical_json(identity).encode()).hexdigest()[:24]
    baseline = _run_replay(
        dataset,
        settings,
        BaselineRestMomentumV2(settings, experiment_id),
    )
    v3 = _run_replay(dataset, settings, EntryV3Strategy(settings, experiment_id))
    deterministic = {
        "identity": identity,
        "baseline": baseline["deterministic"],
        "entry_v3": v3["deterministic"],
    }
    return {
        "experiment_id": experiment_id,
        **identity,
        "methodology": {
            "baseline": "Frozen V2 formulas adapted to each valid normalized L2 book event.",
            "entry_v3": (
                "The same MarketEvent and EntryV3Strategy code used by live PAPER shadow mode."
            ),
            "execution": (
                "Both strategies use the same conservative taker simulator and risk exits."
            ),
            "promotion_scope": "Diagnostic only. One dataset cannot support promotion.",
        },
        "walk_forward": walk_forward_segments(dataset),
        "baseline": baseline,
        "entry_v3": v3,
        "event_hash": hashlib.sha256(_canonical_json(deterministic).encode()).hexdigest(),
    }


def _run_replay(
    dataset: Path,
    settings: Settings,
    strategy: BaselineRestMomentumV2 | EntryV3Strategy,
) -> dict[str, object]:
    simulator = ConservativeReplaySimulator(settings)
    event_count = 0
    for market_event in iter_market_events(dataset, settings):
        event_count += 1
        closed = simulator.mark(market_event)
        if closed is not None:
            strategy.mark_exited(
                str(closed["symbol"]),
                int(str(closed["closed_at_ms"])),
                str(closed["exit_reason"]),
            )
        for decision in strategy.process(market_event):
            if (
                decision.get("event_type") != "entry_candidate"
                or decision.get("decision") != "ACCEPTED"
            ):
                continue
            if simulator.enter(market_event, decision):
                strategy.mark_entered(str(decision["symbol"]), decision)
    closed = simulator.close_at_end()
    if closed is not None:
        strategy.mark_exited(
            str(closed["symbol"]),
            int(str(closed["closed_at_ms"])),
            str(closed["exit_reason"]),
        )
    candidates = [
        event for event in strategy.events if event.get("event_type") == "entry_candidate"
    ]
    metrics = replay_metrics(simulator.trades, candidates)
    deterministic = {
        "strategy_version": (
            BASELINE_STRATEGY_VERSION
            if isinstance(strategy, BaselineRestMomentumV2)
            else ENTRY_V3_STRATEGY_VERSION
        ),
        "market_event_count": event_count,
        "candidates": candidates,
        "trades": simulator.trades,
        "metrics": metrics,
    }
    result: dict[str, object] = {
        "deterministic": deterministic,
        "output_hash": hashlib.sha256(_canonical_json(deterministic).encode()).hexdigest(),
        **metrics,
    }
    if isinstance(strategy, EntryV3Strategy):
        result["diagnostics"] = strategy.diagnostics()
    else:
        result["counters"] = dict(sorted(strategy.counters.items()))
    return result


def replay_metrics(
    trades: Sequence[dict[str, object]],
    candidates: Sequence[dict[str, object]],
) -> dict[str, object]:
    pnl = [Decimal(str(trade["net_pnl_usdt"])) for trade in trades]
    gross = sum((Decimal(str(trade["gross_pnl_usdt"])) for trade in trades), Decimal(0))
    fees = sum((Decimal(str(trade["fees_usdt"])) for trade in trades), Decimal(0))
    spread = sum((Decimal(str(trade["spread_cost_usdt"])) for trade in trades), Decimal(0))
    slippage = sum((Decimal(str(trade["slippage_cost_usdt"])) for trade in trades), Decimal(0))
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_wins = sum(wins, Decimal(0))
    gross_losses = abs(sum(losses, Decimal(0)))
    mfe = [Decimal(str(trade["mfe_bps"])) for trade in trades]
    mae = [abs(Decimal(str(trade["mae_bps"]))) for trade in trades]
    total_mae = sum(mae, Decimal(0))
    reasons = Counter(
        str(candidate.get("rejection_reason"))
        for candidate in candidates
        if candidate.get("decision") == "REJECTED"
    )
    accepted = [candidate for candidate in candidates if candidate.get("decision") == "ACCEPTED"]
    return {
        "trade_count": len(trades),
        "entry_count": len(accepted),
        "rejected_candidates": len(candidates) - len(accepted),
        "gross_pnl_usdt": _number(gross),
        "net_pnl_usdt": _number(sum(pnl, Decimal(0))),
        "fees_usdt": _number(fees),
        "spread_cost_usdt": _number(spread),
        "slippage_cost_usdt": _number(slippage),
        "expectancy_usdt": _number(sum(pnl, Decimal(0)) / len(pnl)) if pnl else None,
        "profit_factor": _number(gross_wins / gross_losses) if gross_losses else None,
        "win_rate": len(wins) / len(pnl) if pnl else None,
        "average_win_usdt": _number(sum(wins, Decimal(0)) / len(wins)) if wins else None,
        "average_loss_usdt": _number(sum(losses, Decimal(0)) / len(losses)) if losses else None,
        "maximum_drawdown_pct": _number(_max_drawdown(pnl)),
        "average_mfe_bps": _number(sum(mfe, Decimal(0)) / len(mfe)) if mfe else None,
        "median_mfe_bps": _number(Decimal(str(statistics.median(mfe)))) if mfe else None,
        "average_mae_bps": _number(sum(mae, Decimal(0)) / len(mae)) if mae else None,
        "median_mae_bps": _number(Decimal(str(statistics.median(mae)))) if mae else None,
        "mfe_mae_ratio": _number(sum(mfe, Decimal(0)) / total_mae) if total_mae else None,
        "average_time_to_mfe_ms": statistics.fmean(
            int(str(trade["time_to_mfe_ms"])) for trade in trades
        )
        if trades
        else None,
        "average_time_to_mae_ms": statistics.fmean(
            int(str(trade["time_to_mae_ms"])) for trade in trades
        )
        if trades
        else None,
        "immediate_adverse_pct": sum(1 for trade in trades if bool(trade["mae_before_mfe"]))
        / len(trades)
        if trades
        else None,
        "immediate_favorable_pct": sum(1 for trade in trades if bool(trade["mfe_before_mae"]))
        / len(trades)
        if trades
        else None,
        "average_stop_overshoot_bps": statistics.fmean(
            float(str(trade["stop_overshoot_bps"]))
            for trade in trades
            if trade.get("exit_reason") == "STOP_LOSS"
        )
        if any(trade.get("exit_reason") == "STOP_LOSS" for trade in trades)
        else None,
        "performance_by_symbol": _trade_breakdown(trades, "symbol"),
        "performance_by_side": _trade_breakdown(trades, "side"),
        "performance_by_regime": _trade_breakdown(trades, "regime_at_entry"),
        "performance_by_entry_type": _trade_breakdown(trades, "entry_type"),
        "rejection_reasons": dict(sorted(reasons.items())),
        "chase_veto_count": reasons["CHASE_TOO_EXTENDED"],
        "exhaustion_veto_count": sum(
            reasons[key]
            for key in (
                "FLOW_DECELERATING",
                "MICROPRICE_DIVERGENCE",
                "SPREAD_EXPANDING",
                "ASK_LIQUIDITY_REBUILD",
                "BID_LIQUIDITY_REBUILD",
            )
        ),
        "chop_rejection_count": reasons["CHOP_REGIME"],
        "cost_rejection_count": reasons["EXPECTED_NET_EDGE_BELOW_HURDLE"],
    }


def walk_forward_segments(dataset: Path) -> dict[str, object]:
    manifest = _read_json(dataset / "manifest.json")
    count = int(str(manifest.get("event_count", 0)))
    development_end = int(count * 0.60)
    validation_end = int(count * 0.80)
    return {
        "method": "CHRONOLOGICAL_60_20_20",
        "development": {"first_sequence": 1, "last_sequence": development_end},
        "validation": {
            "first_sequence": development_end + 1,
            "last_sequence": validation_end,
        },
        "out_of_sample": {
            "first_sequence": validation_end + 1,
            "last_sequence": count,
        },
        "shuffled": False,
        "optimization_run": False,
    }


def freeze_audit_snapshot(
    project_root: Path,
    state_path: Path,
    *,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict[str, object]:
    reader = read_bytes or Path.read_bytes
    for _ in range(3):
        before = reader(state_path)
        state = json.loads(before)
        if not isinstance(state, dict):
            raise ValueError("paper checkpoint is not an object")
        ledger_path = (project_root / str(state.get("event_ledger_path", ""))).resolve()
        if not ledger_path.is_relative_to(project_root.resolve()):
            raise ValueError("paper ledger path escapes the project root")
        ledger = reader(ledger_path)
        after = reader(state_path)
        if before != after:
            continue
        current_ledger = reader(ledger_path)
        if ledger != current_ledger:
            continue
        frozen_state = json.loads(before)
        summary = _scan_frozen_ledger(ledger, str(frozen_state["run_id"]))
        reconcile_checkpoint(frozen_state, summary)
        return {
            "audit_id": _stable_id(
                str(frozen_state["run_id"]),
                str(frozen_state["last_event_sequence"]),
                hashlib.sha256(before).hexdigest(),
                hashlib.sha256(ledger).hexdigest(),
            ),
            "audit_cutoff_utc": _utc_now(),
            "git_commit": _git_commit(project_root),
            "run_id": frozen_state["run_id"],
            "checkpoint_sequence": frozen_state["checkpoint_sequence"],
            "last_event_sequence": frozen_state["last_event_sequence"],
            "trade_count": frozen_state["trades"],
            "ledger_hash": hashlib.sha256(ledger).hexdigest(),
            "checkpoint_hash": hashlib.sha256(before).hexdigest(),
            "config_hash": _file_hash(project_root / "config" / "paper.toml"),
        }
    raise RuntimeError("paper state changed while the audit snapshot was freezing")


def run_live_shadow(
    settings: Settings,
    symbols: Sequence[str],
    output_root: Path,
    duration_seconds: int,
) -> dict[str, object]:
    if settings.mode != "PAPER" or settings.live_trading_enabled:
        raise RuntimeError("Entry V3 live shadow requires PAPER mode with live trading disabled")
    config_hash = _file_hash(DEFAULT_CONFIG_PATH)
    code_hash = _code_hash(DEFAULT_CONFIG_PATH.parent.parent)
    recorder = RawEventRecorder(
        output_root,
        symbols,
        code_revision=code_hash,
        config_revision=config_hash,
    )
    identity = {
        "stream_id": recorder.dataset_id,
        "config_hash": config_hash,
        "working_tree_code_hash": code_hash,
        "strategy_version": ENTRY_V3_STRATEGY_VERSION,
        "execution_model_version": LIVE_EXECUTION_MODEL_VERSION,
    }
    experiment_id = hashlib.sha256(_canonical_json(identity).encode()).hexdigest()[:24]
    normalizer = MarketEventNormalizer(
        recorder.dataset_id,
        book_depth=settings.entry_v3.book_depth,
    )
    strategy = EntryV3Strategy(settings, experiment_id)

    def consume(record: dict[str, object]) -> None:
        for event in normalizer.normalize(record):
            strategy.process(event)

    manifest_path = asyncio.run(
        GateMarketCapture(symbols, recorder, event_sink=consume).run(duration_seconds)
    )
    result: dict[str, object] = {
        "experiment_id": experiment_id,
        **identity,
        "manifest": _read_json(manifest_path),
        "normalizer_counters": dict(sorted(normalizer.counters.items())),
        "diagnostics": strategy.diagnostics(),
        "recent_strategy_events": strategy.events[-200:],
    }
    _atomic_json(settings.log_directory / "entry-v3-status.json", result)
    return result


def write_replay_comparison(
    dataset: Path,
    settings: Settings,
    output_root: Path,
) -> Path:
    result = run_replay_comparison(dataset, settings)
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / f"{result['experiment_id']}.json"
    _atomic_json(path, result)
    _atomic_json(settings.log_directory / "entry-v3-status.json", result)
    return path


def _scan_frozen_ledger(data: bytes, run_id: str) -> LedgerSummary:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        path.write_bytes(data)
        return scan_ledger(path, run_id)


def _trade_breakdown(
    trades: Sequence[dict[str, object]],
    field: str,
) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[Decimal]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(field) or "UNKNOWN")].append(Decimal(str(trade["net_pnl_usdt"])))
    return {
        key: {
            "trades": len(values),
            "net_pnl_usdt": _number(sum(values, Decimal(0))),
            "expectancy_usdt": _number(sum(values, Decimal(0)) / len(values)),
            "win_rate": sum(value > 0 for value in values) / len(values),
        }
        for key, values in sorted(groups.items())
    }


def _max_drawdown(pnl: Sequence[Decimal]) -> Decimal:
    equity = Decimal(0)
    peak = Decimal(0)
    drawdown = Decimal(0)
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _trim(values: deque[tuple[Any, ...]], cutoff: int) -> None:
    while values and int(values[0][0]) < cutoff:
        values.popleft()


def _notional_depth(levels: Sequence[Level]) -> Decimal:
    return sum((price * size for price, size in levels), Decimal(0))


def _depth_change(
    previous: Decimal | None,
    current: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    if previous is None or current is None or previous <= 0:
        return None, None
    delta = (current - previous) / previous
    return max(Decimal(0), -delta), max(Decimal(0), delta)


def _vwap(values: Iterable[tuple[int, Decimal, Decimal, Decimal]]) -> Decimal | None:
    rows = tuple(values)
    volume = sum((buy + sell for _, _, buy, sell in rows), Decimal(0))
    if not volume:
        return None
    return sum((price * (buy + sell) for _, price, buy, sell in rows), Decimal(0)) / volume


def _bps(delta: Decimal, base: Decimal) -> Decimal:
    return delta / base * Decimal(10_000) if base else Decimal(0)


def _agreement(*conditions: bool) -> Decimal:
    return Decimal(sum(conditions)) / Decimal(len(conditions)) if conditions else Decimal(0)


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None and value.is_finite() else None


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_int(value: object, field: str) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed <= 0:
        raise DatasetError(f"{field} must be a positive integer")
    return parsed


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"


def _serialize(value: object) -> object:
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        _serialize(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{path} must contain an object")
    return value


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_hash(project_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((project_root / "autotrade").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_commit(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and len(commit) == 40 else "UNKNOWN"


def _utc_from_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000_000, UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(_canonical_json(value))
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)
