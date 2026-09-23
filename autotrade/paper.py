from __future__ import annotations

import json
import logging
import math
import os
import shutil
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from time import monotonic, time_ns
from typing import Any, TypedDict

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import (
    BacktestEngineConfig,
    LoggingConfig,
    RiskEngineConfig,
    StrategyConfig,
)
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.events import (
    OrderFilled,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from autotrade.ama_control import AmaControlEngine
from autotrade.analytics import aggregate_trades, classify_close, reconstruct_trades
from autotrade.config import Settings
from autotrade.experiments import (
    SizingResult,
    capped_notional_quantity,
    churn_block_reason,
    cost_gate_reason,
    deterministic_candidate_ranking,
    market_quality_reasons,
    partial_runner_quantities,
    risk_adjusted_quantity,
)
from autotrade.integrity import (
    ACCOUNTING_TOLERANCE,
    CHECKPOINT_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    EXECUTION_MODEL_VERSION,
    MARKET_DATA_MODE,
    STRATEGY_VERSION,
    TRANSITION_PROTOCOL_VERSION,
    EventLedger,
    EventSpec,
    IntegrityError,
    commit_transition,
    create_new_run,
    new_identity,
    reconcile_checkpoint,
    recover_pending_transition,
    scan_ledger,
    utc_now,
)
from autotrade.market_scope import (
    MarketScope,
    MarketScopeController,
    SwitchState,
    XauDirection,
    XauSignalEngine,
)
from autotrade.runtime import RuntimeReport
from autotrade.time_utils import WIB, canonical_utc, duration_ms

TAKER_FEE = Decimal("0.0005")
SAFETY_BUFFER_BPS = 3.0
TRADE_HISTORY_HOURS = 48
STATE_SCHEMA_VERSION = CHECKPOINT_SCHEMA_VERSION
MIN_FREE_DISK_BYTES = 1_000_000_000


class QuoteValidationError(ValueError):
    pass


class MomentumConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: Decimal
    size_precision: int
    size_increment: Decimal
    window: int
    persistence_ticks: int
    regime_window: int
    entry_threshold_bps: float
    minimum_net_edge_bps: float
    minimum_confidence: float
    stop_loss_bps: float
    take_profit_bps: float
    max_hold_ns: int
    cooldown_ns: int
    signal_reset_reentry: bool
    exit_variant: str
    runner_trail_bps: float
    runner_max_hold_ns: int
    edge_decay_time_exit: bool


class RestMomentumStrategy(Strategy):
    """Cost-aware REST-quote momentum challenger for paper execution only."""

    def __init__(self, config: MomentumConfig) -> None:
        super().__init__(config)
        self.entry_enabled = False
        self.status = "WARMING_UP"
        self.expected_gross_bps = 0.0
        self.expected_cost_bps = 0.0
        self.expected_net_bps = 0.0
        self.confidence = 0.0
        self.entry_candidate = False
        self.last_signal: str | None = None
        self.trades = 0
        self.fees_usdt = Decimal(0)
        self.entry_fee_usdt = Decimal(0)
        self.open_entry_fee_usdt = Decimal(0)
        self._history: deque[float] = deque(maxlen=config.regime_window + 1)
        self._entry_price: float | None = None
        self._entry_quantity = Decimal(0)
        self._entry_notional = Decimal(0)
        self._exit_quantity = Decimal(0)
        self._exit_notional = Decimal(0)
        self._exit_fee_usdt = Decimal(0)
        self._entry_ts: int | None = None
        self._position_side: PositionSide | None = None
        self._exit_reason: str | None = None
        self._last_exit_ts = 0
        self._pending_order = False
        self._flatten_reason: str | None = None
        self._candidate_side: OrderSide | None = None
        self._regime_move_bps = 0.0
        self._prior_regime_move_bps = 0.0
        self._market_context: dict[str, Decimal] = {}
        self._signal_reset_required = False
        self._stopped_direction: str | None = None
        self.blocked_reason: str | None = None
        self._position_id: object | None = None
        self._runner_active = False
        self._runner_peak_bps = 0.0
        self._mfe_bps = 0.0
        self._mae_bps = 0.0
        self._time_to_mfe_ms = 0
        self._time_to_mae_ms = 0
        self._reduction_realized_pnl = Decimal(0)
        self._events: list[dict[str, object]] = []
        self._entry_metadata: dict[str, object] = {}

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.config.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        bid = tick.bid_price.as_double()
        ask = tick.ask_price.as_double()
        mid = (bid + ask) / 2
        self._history.append(mid)
        self.entry_candidate = False
        self.confidence = 0.0
        self._candidate_side = None
        self.blocked_reason = None

        if self._entry_price is not None and not self._pending_order:
            if self._flatten_reason is not None:
                self._close(self._flatten_reason)
                return
            direction = 1 if self._position_side == PositionSide.LONG else -1
            move_bps = (mid / self._entry_price - 1) * 10_000 * direction
            age_ns = tick.ts_event - (self._entry_ts or tick.ts_event)
            age_ms = max(0, age_ns // 1_000_000)
            if move_bps > self._mfe_bps:
                self._mfe_bps = move_bps
                self._time_to_mfe_ms = age_ms
            if move_bps < self._mae_bps:
                self._mae_bps = move_bps
                self._time_to_mae_ms = age_ms
            momentum_bps = 0.0
            if len(self._history) > self.config.window:
                momentum_bps = (
                    (mid / self._history[-self.config.window - 1] - 1) * 10_000 * direction
                )
            if move_bps <= -self.config.stop_loss_bps:
                self._close("STOP_LOSS")
            elif self._runner_active and move_bps <= (
                self._runner_peak_bps - self.config.runner_trail_bps
            ):
                self._close("RUNNER_TRAIL")
            elif self._runner_active and momentum_bps <= -self.config.entry_threshold_bps / 2:
                self._close("RUNNER_SIGNAL_REVERSED")
            elif self._runner_active and age_ns >= self.config.runner_max_hold_ns:
                self._close("RUNNER_TIME_EXIT")
            elif move_bps >= self.config.take_profit_bps:
                if self.config.exit_variant == "BASELINE_FULL_TP" or self._runner_active:
                    if self._runner_active:
                        self._runner_peak_bps = max(self._runner_peak_bps, move_bps)
                    else:
                        self._close("TAKE_PROFIT")
                else:
                    self._start_runner("TAKE_PROFIT_PARTIAL")
            elif age_ns >= self.config.max_hold_ns:
                if (
                    self.config.edge_decay_time_exit
                    and self.config.exit_variant != "BASELINE_FULL_TP"
                    and move_bps > 0
                    and momentum_bps > 0
                    and not self._runner_active
                ):
                    self._start_runner("EDGE_DECAY_PARTIAL")
                else:
                    self._close("TIME_EXIT")
            elif self._runner_active:
                self._runner_peak_bps = max(self._runner_peak_bps, move_bps)
            return

        if self._pending_order:
            self.status = "POSITION_OPEN" if self._entry_price is not None else "ORDER_PENDING"
            return
        if tick.ts_event < self._last_exit_ts + self.config.cooldown_ns:
            self.status = "COOLDOWN"
            return
        minimum_history = self.config.regime_window + 1
        if len(self._history) < minimum_history:
            self.status = f"WARMING_UP_{len(self._history)}/{minimum_history}"
            return

        history = tuple(self._history)
        signal_start = history[-self.config.window - 1]
        regime_start = history[0]
        move_bps = (mid / signal_start - 1) * 10_000
        prior_regime_move_bps = (signal_start / regime_start - 1) * 10_000
        regime_move_bps = (mid / regime_start - 1) * 10_000
        self._regime_move_bps = regime_move_bps
        self._prior_regime_move_bps = prior_regime_move_bps
        spread_bps = (ask - bid) / mid * 10_000
        cost_bps = 10.0 + spread_bps + SAFETY_BUFFER_BPS
        self.expected_gross_bps = abs(move_bps)
        self.expected_cost_bps = cost_bps
        self.expected_net_bps = self.expected_gross_bps - cost_bps
        self.confidence = min(
            1.0,
            max(0.0, self.expected_net_bps / max(self.config.entry_threshold_bps, 1.0)),
        )
        hurdle = max(self.config.entry_threshold_bps, cost_bps)
        if (
            abs(move_bps) <= hurdle
            or self.expected_net_bps < self.config.minimum_net_edge_bps
            or self.confidence < self.config.minimum_confidence
        ):
            if abs(move_bps) <= hurdle:
                self._signal_reset_required = False
                self._stopped_direction = None
            self.status = "WAITING_EDGE"
            return

        side = OrderSide.BUY if move_bps > 0 else OrderSide.SELL
        direction_name = "LONG" if side == OrderSide.BUY else "SHORT"
        if (
            self.config.signal_reset_reentry
            and self._signal_reset_required
            and self._stopped_direction == direction_name
        ):
            self.status = "BLOCKED_SIGNAL_RESET"
            self.blocked_reason = "SIGNAL_RESET_REQUIRED"
            return
        if self._signal_reset_required and self._stopped_direction != direction_name:
            self._signal_reset_required = False
            self._stopped_direction = None
        direction = 1 if move_bps > 0 else -1
        persistence_prices = history[-self.config.persistence_ticks - 1 :]
        persistence_ok = all(
            (current - previous) * direction > 0
            for previous, current in zip(persistence_prices, persistence_prices[1:], strict=False)
        )
        prior_regime_min_bps = self.config.entry_threshold_bps / 2
        regime_ok = (
            prior_regime_move_bps * direction >= prior_regime_min_bps
            and regime_move_bps * direction >= self.config.entry_threshold_bps
        )
        if not persistence_ok or not regime_ok:
            self.status = "WAITING_CONFIRMATION"
            return

        self.entry_candidate = True
        self._candidate_side = side
        self.status = "ELIGIBLE" if self.entry_enabled else "ELIGIBLE_PAUSED"

    @property
    def has_position(self) -> bool:
        return self._entry_price is not None

    @property
    def has_pending_order(self) -> bool:
        return self._pending_order

    @property
    def signal_has_reset(self) -> bool:
        return not self._signal_reset_required

    @property
    def candidate_side(self) -> OrderSide | None:
        return self._candidate_side

    def candidate_entry_price(self) -> Decimal | None:
        if self._candidate_side == OrderSide.BUY:
            return self._market_context.get("modeled_ask")
        if self._candidate_side == OrderSide.SELL:
            return self._market_context.get("modeled_bid")
        return None

    def set_market_context(
        self,
        *,
        raw_bid: Decimal,
        raw_ask: Decimal,
        modeled_bid: Decimal,
        modeled_ask: Decimal,
    ) -> None:
        self._market_context = {
            "raw_bid": raw_bid,
            "raw_ask": raw_ask,
            "raw_mid": (raw_bid + raw_ask) / 2,
            "modeled_bid": modeled_bid,
            "modeled_ask": modeled_ask,
        }

    def apply_external_decision(
        self,
        *,
        direction: str,
        confidence: Decimal,
        gross_edge_bps: Decimal,
        cost_bps: Decimal,
        metadata: dict[str, object],
    ) -> None:
        self.entry_candidate = False
        self._candidate_side = None
        self.confidence = float(confidence)
        self.expected_gross_bps = float(gross_edge_bps)
        self.expected_cost_bps = float(cost_bps)
        self.expected_net_bps = float(gross_edge_bps - cost_bps)
        self._entry_metadata = dict(metadata)
        if self.has_position or self.has_pending_order:
            return
        if direction not in {"LONG", "SHORT"}:
            self.status = "WAITING_XAU_SIGNAL"
            return
        self._candidate_side = OrderSide.BUY if direction == "LONG" else OrderSide.SELL
        self.entry_candidate = True
        self.status = "ELIGIBLE" if self.entry_enabled else "ELIGIBLE_PAUSED"

    def submit_candidate(
        self,
        quantity: Decimal | None = None,
        *,
        intended_risk_usdt: Decimal | None = None,
        candidate_rank: int = 1,
        candidate_set: list[dict[str, object]] | None = None,
    ) -> bool:
        side = self._candidate_side
        if (
            not self.entry_enabled
            or not self.entry_candidate
            or side is None
            or self._entry_price is not None
            or self._pending_order
        ):
            return False

        order_quantity = quantity or self.config.trade_size
        if order_quantity <= 0:
            self.status = "QUANTITY_REJECTED"
            self.blocked_reason = "ZERO_SAFE_QUANTITY"
            return False
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=Quantity(order_quantity, self.config.size_precision),
            time_in_force=TimeInForce.GTC,
        )
        self._pending_order = True
        self.status = "ENTRY_SUBMITTED"
        self.last_signal = "LONG" if side == OrderSide.BUY else "SHORT"
        self._event(
            "signal",
            direction=self.last_signal,
            gross_edge_bps=round(self.expected_gross_bps, 3),
            cost_bps=round(self.expected_cost_bps, 3),
            net_edge_bps=round(self.expected_net_bps, 3),
            confidence=round(self.confidence, 4),
            persistence_ticks=self.config.persistence_ticks,
            prior_regime_move_bps=round(self._prior_regime_move_bps, 3),
            regime_move_bps=round(self._regime_move_bps, 3),
            stop_distance_bps=self.config.stop_loss_bps,
            intended_risk_usdt=str(intended_risk_usdt) if intended_risk_usdt is not None else None,
            candidate_rank=candidate_rank,
            candidate_set=candidate_set or [],
            entry_spread_bps=round(
                float(
                    (
                        self._market_context.get("raw_ask", Decimal(0))
                        - self._market_context.get("raw_bid", Decimal(0))
                    )
                    / self._market_context.get("raw_mid", Decimal(1))
                    * Decimal(10_000)
                ),
                3,
            ),
            **self._entry_metadata,
        )
        self._event(
            "order_submitted",
            side=self.last_signal,
            quantity=str(order_quantity),
            order_type="MARKET",
            client_order_id=str(order.client_order_id),
        )
        self.submit_order(order)
        return True

    def on_order_filled(self, event: OrderFilled) -> None:
        quantity = event.last_qty.as_decimal()
        price = event.last_px.as_decimal()
        fee = event.commission.as_decimal()
        order_side = event.order_side.name
        raw_bid = self._market_context.get("raw_bid")
        raw_ask = self._market_context.get("raw_ask")
        raw_mid = self._market_context.get("raw_mid")
        spread_cost: Decimal | None = None
        slippage_cost: Decimal | None = None
        if raw_bid is not None and raw_ask is not None and raw_mid is not None:
            if order_side == "BUY":
                spread_cost = max(Decimal(0), raw_ask - raw_mid) * quantity
                slippage_cost = max(Decimal(0), price - raw_ask) * quantity
            else:
                spread_cost = max(Decimal(0), raw_mid - raw_bid) * quantity
                slippage_cost = max(Decimal(0), raw_bid - price) * quantity
        self.fees_usdt += fee
        if self._exit_reason is None:
            self._entry_quantity += quantity
            self._entry_notional += quantity * price
            self.entry_fee_usdt += fee
            self.open_entry_fee_usdt += fee
            if self._entry_price is not None:
                self._entry_price = float(self._entry_notional / self._entry_quantity)
        else:
            self._exit_quantity += quantity
            self._exit_notional += quantity * price
            self._exit_fee_usdt += fee
        self._event(
            "fill",
            side=order_side,
            quantity=str(quantity),
            price=str(price),
            fee_usdt=str(fee),
            client_order_id=str(event.client_order_id),
            exchange_timestamp=_ns_to_utc(event.ts_event),
            maker_taker="TAKER",
            spread_cost_usdt=str(spread_cost) if spread_cost is not None else None,
            modeled_slippage_usdt=str(slippage_cost) if slippage_cost is not None else None,
            slippage_embedded_in_fill_price=True,
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        self._entry_price = (
            float(self._entry_notional / self._entry_quantity)
            if self._entry_quantity
            else event.avg_px_open
        )
        self._entry_ts = event.ts_event
        self._position_side = event.side
        self._position_id = event.position_id
        self._pending_order = False
        self.entry_candidate = False
        self.confidence = 0.0
        self._candidate_side = None
        self.status = f"POSITION_{event.side.name}"
        self._event(
            "position_opened",
            side=event.side.name,
            quantity=str(self._entry_quantity),
            price=str(self._entry_price),
            exchange_timestamp=_ns_to_utc(event.ts_event),
        )

    def on_position_changed(self, event: PositionChanged) -> None:
        if self._exit_reason is None:
            self._position_id = event.position_id
            self.status = f"POSITION_{event.side.name}"
            return
        self._pending_order = False
        self._runner_active = True
        self.status = f"RUNNER_{event.side.name}"
        remaining = event.quantity.as_decimal()
        self.open_entry_fee_usdt = (
            self.entry_fee_usdt * remaining / self._entry_quantity
            if self._entry_quantity
            else Decimal(0)
        )
        cumulative_realized = event.realized_pnl.as_decimal() + self.open_entry_fee_usdt
        realized_delta = cumulative_realized - self._reduction_realized_pnl
        self._reduction_realized_pnl = cumulative_realized
        self._event(
            "position_reduced",
            side=event.side.name,
            quantity=str(event.quantity.as_decimal()),
            price=str(event.last_px.as_decimal()),
            reason=self._exit_reason,
            realized_pnl_usdt=str(realized_delta),
            exchange_timestamp=_ns_to_utc(event.ts_event),
        )

    def on_position_closed(self, event: PositionClosed) -> None:
        total_realized = event.realized_pnl.as_decimal()
        realized = total_realized - self._reduction_realized_pnl
        stopped_out = self._exit_reason == "STOP_LOSS"
        close_price = (
            float(self._exit_notional / self._exit_quantity) if self._exit_quantity else None
        )
        self._event(
            "position_closed",
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            quantity=str(self._entry_quantity),
            open_price=str(self._entry_price),
            close_price=str(close_price),
            price=str(close_price),
            realized_pnl_usdt=str(realized),
            total_trade_pnl_usdt=str(total_realized),
            pnl_pct=(
                str(total_realized / self._entry_notional * 100) if self._entry_notional else "0"
            ),
            fee_usdt=str(self.entry_fee_usdt + self._exit_fee_usdt),
            reason=self._exit_reason or "ENGINE_CLOSE",
            exchange_timestamp=_ns_to_utc(event.ts_event),
            entry_notional_usdt=str(self._entry_notional),
            exit_notional_usdt=str(self._exit_notional),
            gross_price_pnl_usdt=str(
                self._exit_notional - self._entry_notional
                if self._position_side == PositionSide.LONG
                else self._entry_notional - self._exit_notional
            ),
            entry_fee_usdt=str(self.entry_fee_usdt),
            exit_fee_usdt=str(self._exit_fee_usdt),
            maker_taker="TAKER",
            funding_usdt="0",
            funding_status="NOT_MODELED",
            other_execution_adjustments_usdt="0",
            mfe_bps=round(self._mfe_bps, 3),
            mae_bps=round(self._mae_bps, 3),
            time_to_mfe_ms=self._time_to_mfe_ms,
            time_to_mae_ms=self._time_to_mae_ms,
        )
        self.trades += 1
        self._last_exit_ts = event.ts_event
        self._entry_price = None
        self._entry_quantity = Decimal(0)
        self._entry_notional = Decimal(0)
        self.entry_fee_usdt = Decimal(0)
        self.open_entry_fee_usdt = Decimal(0)
        self._exit_quantity = Decimal(0)
        self._exit_notional = Decimal(0)
        self._exit_fee_usdt = Decimal(0)
        self._entry_ts = None
        self._position_side = None
        self._exit_reason = None
        self._pending_order = False
        self._flatten_reason = None
        if stopped_out:
            self._signal_reset_required = True
            self._stopped_direction = self.last_signal
        self._position_id = None
        self._runner_active = False
        self._runner_peak_bps = 0.0
        self._mfe_bps = 0.0
        self._mae_bps = 0.0
        self._time_to_mfe_ms = 0
        self._time_to_mae_ms = 0
        self._reduction_realized_pnl = Decimal(0)
        self.entry_candidate = False
        self.confidence = 0.0
        self._candidate_side = None
        self.status = "COOLDOWN"

    def on_order_rejected(self, event: object) -> None:
        self._pending_order = False
        self.status = "ORDER_REJECTED"
        self._event("order_rejected", reason=str(event)[:300])

    def on_stop(self) -> None:
        self.unsubscribe_quote_ticks(self.config.instrument_id)

    def request_flatten(self, reason: str = "MANUAL_FLATTEN") -> bool:
        if self._entry_price is None:
            return False
        self._flatten_reason = reason
        return True

    def drain_events(self) -> list[dict[str, object]]:
        events, self._events = self._events, []
        return events

    def _close(self, reason: str) -> None:
        self._pending_order = True
        self.status = f"EXIT_{reason}"
        self._exit_reason = reason
        self._event(
            "exit_signal",
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            quantity=str(self._entry_quantity),
            reason=reason,
        )
        self.close_all_positions(self.config.instrument_id, reduce_only=True)

    def _start_runner(self, reason: str) -> None:
        fraction = (
            Decimal("0.75") if self.config.exit_variant == "TP75_RUNNER25" else Decimal("0.50")
        )
        try:
            initial, runner = partial_runner_quantities(
                self._entry_quantity,
                fraction,
                self.config.size_increment,
            )
        except ValueError:
            self._close("TAKE_PROFIT")
            return
        if runner <= 0 or self._position_id is None:
            self._close("TAKE_PROFIT")
            return
        closing_side = OrderSide.SELL if self._position_side == PositionSide.LONG else OrderSide.BUY
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=closing_side,
            quantity=Quantity(initial, self.config.size_precision),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
        )
        self._pending_order = True
        self._exit_reason = reason
        self._runner_peak_bps = max(self._runner_peak_bps, self.config.take_profit_bps)
        self.status = f"EXIT_{reason}"
        self._event(
            "exit_signal",
            side=self._position_side.name if self._position_side is not None else "UNKNOWN",
            quantity=str(initial),
            reason=reason,
            runner_quantity=str(runner),
        )
        self.submit_order(order, position_id=self._position_id)

    def _event(self, event_type: str, **values: object) -> None:
        self._events.append(
            {
                "event": event_type,
                "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **values,
            }
        )


class PortfolioSnapshot(TypedDict):
    equity_usdt: float
    daily_pnl_usdt: float
    drawdown_pct: float
    trades_today: int
    open_positions: int
    fees_usdt: float
    slippage_usdt: float | None


@dataclass(frozen=True)
class PaperSnapshot:
    portfolio: PortfolioSnapshot
    strategy: dict[str, object]
    orders: int
    positions: int
    risk_halted: bool
    alerts: list[str]
    open_trade: dict[str, object] | None = None
    trade_history: list[dict[str, object]] = field(default_factory=list)
    diagnostics: dict[str, Mapping[str, object]] = field(default_factory=dict)


class PaperTrader:
    """Owns the long-running Nautilus paper engine; the dashboard only reads it."""

    def __init__(
        self,
        report: RuntimeReport,
        settings: Settings,
        logger: logging.Logger,
        scope_controller: MarketScopeController | None = None,
    ) -> None:
        self._report = report
        self._settings = settings
        self._logger = logger
        self._scope = scope_controller or MarketScopeController(
            settings.log_directory / "market-scope.json",
            settings.log_directory / "market-scope-events.jsonl",
            settings.market_scope,
        )
        self._xau_engine = XauSignalEngine(settings.xau)
        self._xau_contract_valid = False
        self._xau_contract_reason = "Gate contract metadata has not been validated"
        self._engine: BacktestEngine | None = None
        self._engine_verified = False
        self._strategies: dict[str, RestMomentumStrategy] = {}
        self._instruments: dict[str, CryptoPerpetual] = {}
        self._last_ts = 0
        self._last_marks: dict[str, Price] = {}
        self._last_markets: dict[str, dict[str, object]] = {}
        self._last_market_monotonic: dict[str, float] = {}
        self._active_clock = monotonic()
        self._active_state = False
        self._strategy_active_seconds = Decimal(0)
        self._rejection_counts: dict[str, int] = {}
        self._entry_enabled = False
        self._selected_symbol: str | None = None
        self._risk_halted = False
        self._risk_halt_reason: str | None = None
        self._peak_equity = settings.starting_balance_usdt
        self._project_root = (
            settings.log_directory.parent
            if settings.log_directory.name.lower() == "logs"
            else settings.log_directory
        )
        self._legacy_events_path = settings.log_directory / "paper-events.jsonl"
        self._events_path = self._legacy_events_path
        self._state_path = settings.log_directory / "paper-state.json"
        self._checkpoint_existed_at_start = self._state_path.exists()
        self._restart_detected_at_utc = utc_now() if self._checkpoint_existed_at_start else None
        self._last_successful_heartbeat_utc: str | None = None
        self._outage_started_at_utc: str | None = None
        self._outage_duration_ms: int | None = None
        self._position_exposed_during_outage = False
        self._recovery_action = "NONE"
        self._recovery_completed_at_utc: str | None = None
        self._trade_history = self._load_trade_history()
        self._analytics_trades: list[dict[str, object]] = []
        self._run_id: str | None = None
        self._session_id = new_identity()
        self._ledger: EventLedger | None = None
        self._pending_event_specs: list[EventSpec] = []
        self._metadata: dict[str, object] = {}
        self._checkpoint_sequence = 0
        self._starting_equity = settings.starting_balance_usdt
        self._cumulative_realized_pnl = Decimal(0)
        self._accounting_state = "INVALID"
        self._accounting_error: str | None = None
        self._rollover_review_required = False
        self._lifecycle: dict[str, dict[str, object | None]] = {}
        self._symbol_errors: dict[str, str] = {}
        self._initial_balance = settings.starting_balance_usdt
        self._restored_trades = 0
        self._restored_fees = Decimal(0)
        self._risk_day_utc = datetime.now(UTC).date().isoformat()
        self._day_start_equity = settings.starting_balance_usdt
        self._trades_at_day_start = 0
        self._recovery_position: dict[str, object] | None = None
        self._state_error: str | None = None
        self._load_state()
        if self._run_id is not None:
            self._trade_history = self._load_trade_history()
            self._analytics_trades = self._load_analytics_trades()
        self._ama_control = AmaControlEngine(
            settings.ama_control,
            project_root=self._project_root,
            run_id=self._run_id or f"UNATTRIBUTED-{self._session_id}",
            session_id=self._session_id,
        )
        self._ama_control.set_external_benchmark(
            "CURRENT_PAPER_BASELINE", self._paper_baseline_benchmark()
        )
        self._ama_control_error: str | None = None
        self._session_start_equity = self._initial_balance
        self._session_peak_equity = self._initial_balance

    @property
    def monitored_symbols(self) -> tuple[str, ...]:
        recovery_position = self._recovery_position
        if recovery_position is not None:
            return (str(recovery_position["symbol"]),)
        return tuple(dict.fromkeys((*self._strategies, *self._scope.required_symbols)))

    @property
    def shadow_symbols(self) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol in self.monitored_symbols
            if symbol not in self._scope.required_symbols
        )

    @property
    def scope_controller(self) -> MarketScopeController:
        return self._scope

    def recheck_integrity(self) -> bool:
        """Re-read durable evidence; never reset a run or clear a live execution fault."""
        if self._state_error is not None and self._ledger is not None:
            return False
        try:
            if not self._state_path.is_file():
                raise IntegrityError("checkpoint is missing; automatic reset is forbidden")
            if self._engine is None:
                self._load_state()
            else:
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
                summary = scan_ledger(self._events_path, str(self._run_id))
                if summary.legacy_count or self._ledger is None:
                    raise IntegrityError("current run has no attributable ledger")
                if summary.last_sequence != self._ledger.last_sequence:
                    raise IntegrityError("durable ledger changed outside the running engine")
                reconcile_checkpoint(state, summary)
                if self._state_error is None:
                    self._persist_state()
        except (OSError, ValueError, TypeError, KeyError, InvalidOperation) as exc:
            self._set_state_invalid(str(exc))
        self._logger.info(
            "Accounting recheck: %s; reason=%s", self._accounting_state, self._accounting_error
        )
        return self._accounting_state == "VALID" and self._state_error is None

    def process(self, markets: list[dict[str, object]], *, entry_enabled: bool) -> None:
        self._advance_active_clock()
        current_markets = {
            str(item["symbol"]): item for item in markets if isinstance(item.get("symbol"), str)
        }
        self._last_markets.update(current_markets)
        received = monotonic()
        self._last_market_monotonic.update(dict.fromkeys(current_markets, received))
        xau_decision = self._xau_engine.update(current_markets, now=received)
        try:
            self._ama_control.set_external_benchmark(
                "CURRENT_PAPER_BASELINE", self._paper_baseline_benchmark()
            )
            self._ama_control.update(current_markets, observed_at=received)
            self._ama_control_error = None
        except Exception as exc:
            self._ama_control_error = str(exc)[:200]
            self._logger.exception("AMA control update failed")
        if (
            self._recovery_position is not None
            or self._state_error is not None
            or self._accounting_state != "VALID"
        ):
            self._active_state = False
            return
        if self._engine is None:
            selected = [
                market
                for market in markets
                if bool(market.get("selected"))
                and str(market.get("symbol")) not in self._scope.required_symbols
            ][: self._settings.active_symbols]
            xau_market = current_markets.get(self._settings.xau.execution_symbol)
            if xau_market is not None:
                selected.append(xau_market)
            if selected:
                self._initialize(selected)
        if self._engine is None or not self._strategies:
            return

        open_positions = self._engine.cache.positions_open()
        self._scope.reconcile(
            open_positions=len(open_positions),
            accounting_valid=self._accounting_state == "VALID",
        )
        scope_status = self._scope.snapshot(len(open_positions))
        if scope_status["switch_status"] == SwitchState.FLATTENING.value:
            for strategy in self._strategies.values():
                if strategy.has_position:
                    strategy.request_flatten("MARKET_SCOPE_SWITCH")

        if shutil.disk_usage(self._settings.log_directory.parent).free < MIN_FREE_DISK_BYTES:
            self._risk_halted = True
            self._risk_halt_reason = "STORAGE_LOW"

        self._apply_strategy_evidence_halt()
        self._entry_enabled = (
            entry_enabled
            and self._settings.strategy_enabled
            and not self._risk_halted
            and self._accounting_state == "VALID"
        )
        self._active_state = self._entry_enabled
        quotes: list[QuoteTick] = []
        entry_scope = self._scope.entry_scope
        for symbol, strategy in self._strategies.items():
            market = current_markets.get(symbol)
            scope_allowed = (
                entry_scope == MarketScope.XAU_ONLY
                and symbol == self._settings.xau.execution_symbol
            ) or (
                entry_scope == MarketScope.WIDE_CRYPTO
                and symbol not in self._scope.required_symbols
            )
            strategy.entry_enabled = bool(
                self._entry_enabled
                and scope_allowed
                and market is not None
                and (
                    symbol == self._settings.xau.execution_symbol
                    or market.get("selected")
                )
            )
            if market is None:
                strategy.status = "WAITING_MARKET"
                continue
            try:
                quote = self._quote(symbol, market)
            except (QuoteValidationError, InvalidOperation, ValueError) as exc:
                strategy.entry_enabled = False
                strategy.status = "QUARANTINED"
                self._symbol_errors[symbol] = str(exc)[:160]
                continue
            self._symbol_errors.pop(symbol, None)
            quotes.append(quote)
        if not quotes:
            if self._engine_verified:
                self._persist_state()
            return
        try:
            self._engine.add_data(quotes)
            self._engine.run(streaming=True)
            self._engine.clear_data()
            if not self._engine_verified:
                self._verify_engine_accounting()
                self._engine_verified = True

            for symbol, strategy in self._strategies.items():
                self._write_events(strategy.drain_events(), symbol)

            xau_strategy = self._strategies.get(self._settings.xau.execution_symbol)
            if xau_strategy is not None:
                xau_strategy.apply_external_decision(
                    direction=xau_decision.direction.value,
                    confidence=xau_decision.confidence,
                    gross_edge_bps=xau_decision.gross_edge_bps,
                    cost_bps=xau_decision.cost_bps,
                    metadata={
                        "market_scope": MarketScope.XAU_ONLY.value,
                        "market_class": "xau",
                        "strategy": "xau_trend_v1",
                        "entry_reason": ",".join(xau_decision.reasons),
                        "regime": xau_decision.regime.value,
                        "risk_profile": "XAU",
                        "mode_at_entry": self._report.mode,
                        "confirmation_state": xau_decision.confirmation,
                        "session": "UNCLASSIFIED",
                    },
                )

            positions = self._engine.cache.positions_open()
            self._scope.reconcile(
                open_positions=len(positions),
                accounting_valid=self._accounting_state == "VALID",
            )
            if len(positions) > 1:
                self._risk_halted = True
                self._risk_halt_reason = "POSITION_INVARIANT"
                raise RuntimeError("paper portfolio invariant failed: more than one open position")
            if not positions and not any(
                strategy.has_pending_order for strategy in self._strategies.values()
            ):
                candidates = [
                    (symbol, strategy)
                    for symbol, strategy in self._strategies.items()
                    if strategy.entry_candidate and strategy.entry_enabled
                ]
                if candidates:
                    eligible: list[tuple[str, RestMomentumStrategy]] = []
                    for symbol, strategy in candidates:
                        reason = self._experimental_block_reason(
                            symbol,
                            strategy,
                            current_markets[symbol],
                        )
                        if reason is not None:
                            strategy.status = f"BLOCKED_{reason}"
                            strategy.blocked_reason = reason
                            self._rejection_counts[reason] = (
                                self._rejection_counts.get(reason, 0) + 1
                            )
                        else:
                            eligible.append((symbol, strategy))
                    if not eligible:
                        self._persist_state()
                        return
                    ranked = self._rank_candidates(eligible, current_markets)
                    symbol, winner = ranked[0]
                    guard_reason = self._entry_guard_reason(
                        symbol,
                        winner,
                        current_markets[symbol],
                    )
                    if guard_reason is not None:
                        winner.status = f"BLOCKED_{guard_reason}"
                        winner.blocked_reason = guard_reason
                        self._rejection_counts[guard_reason] = (
                            self._rejection_counts.get(guard_reason, 0) + 1
                        )
                        self._persist_state()
                        return
                    entry_price = winner.candidate_entry_price()
                    if entry_price is None:
                        winner.status = "BLOCKED_NO_MODELED_ENTRY_PRICE"
                        winner.blocked_reason = "NO_MODELED_ENTRY_PRICE"
                        self._rejection_counts["NO_MODELED_ENTRY_PRICE"] = (
                            self._rejection_counts.get("NO_MODELED_ENTRY_PRICE", 0) + 1
                        )
                        self._persist_state()
                        return
                    sizing = self._entry_sizing(
                        entry_price,
                        symbol=symbol,
                        size_factor=(
                            xau_decision.size_factor
                            if symbol == self._settings.xau.execution_symbol
                            else Decimal(1)
                        ),
                    )
                    if sizing.reason is not None:
                        winner.status = f"BLOCKED_{sizing.reason}"
                        winner.blocked_reason = sizing.reason
                        self._rejection_counts[sizing.reason] = (
                            self._rejection_counts.get(sizing.reason, 0) + 1
                        )
                        self._persist_state()
                        return
                    candidate_set = [
                        {
                            "symbol": ranked_symbol,
                            "rank": index,
                            "confidence": round(ranked_strategy.confidence, 4),
                            "expected_net_bps": round(ranked_strategy.expected_net_bps, 3),
                            "spread_bps": current_markets[ranked_symbol].get("spread_bps"),
                        }
                        for index, (ranked_symbol, ranked_strategy) in enumerate(ranked, 1)
                    ]
                    self._selected_symbol = symbol
                    if winner.submit_candidate(
                        sizing.quantity,
                        intended_risk_usdt=sizing.intended_risk_usdt,
                        candidate_rank=1,
                        candidate_set=candidate_set,
                    ):
                        self._write_events(winner.drain_events(), symbol)
                        self._engine.add_data([self._quote(symbol, current_markets[symbol])])
                        self._engine.run(streaming=True)
                        self._engine.clear_data()
                        self._write_events(winner.drain_events(), symbol)

            self._apply_risk_limits()
            self._persist_state()
        except Exception:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "EXECUTION_INTEGRITY"
            self._entry_enabled = False
            for symbol, strategy in self._strategies.items():
                self._write_events(strategy.drain_events(), symbol)
            self._persist_state()
            raise

    def set_entry_enabled(self, enabled: bool) -> None:
        self._advance_active_clock()
        self._entry_enabled = (
            enabled and not self._risk_halted and self._accounting_state == "VALID"
        )
        self._active_state = self._entry_enabled
        for symbol, strategy in self._strategies.items():
            market = self._last_markets.get(symbol)
            strategy.entry_enabled = bool(
                self._entry_enabled and market is not None and market.get("selected")
            )

    def _advance_active_clock(self) -> None:
        now = monotonic()
        elapsed = max(0.0, now - self._active_clock)
        self._active_clock = now
        if self._active_state:
            self._strategy_active_seconds += Decimal(
                str(min(elapsed, float(self._settings.market_stale_after_seconds)))
            )

    def authorize_resume(self) -> bool:
        if self._accounting_state != "VALID" or self._recovery_position is not None:
            return False
        if self._rollover_review_required and self._risk_halt_reason in {
            "DAILY_LOSS_REVIEW",
            "MAX_DRAWDOWN_REVIEW",
        }:
            self._rollover_review_required = False
            self._risk_halted = False
            self._risk_halt_reason = None
            self._persist_state()
        return not self._risk_halted

    def flatten(self) -> bool:
        if self._state_error is not None or self._accounting_state != "VALID":
            return False
        if self._recovery_position is not None:
            self._flatten_recovery()
            return True
        return any(strategy.request_flatten() for strategy in self._strategies.values())

    def snapshot(self) -> PaperSnapshot:
        if self._state_error is not None:
            return self._inactive_snapshot(
                status="STATE_INVALID",
                positions=0,
                alert=f"Persistent paper state is invalid: {self._state_error}",
            )
        if self._recovery_position is not None:
            return self._inactive_snapshot(
                status="RECOVERY_REQUIRED",
                positions=1,
                alert="A persisted paper position requires manual FLATTEN before resume.",
            )
        if self._engine is None or not self._strategies:
            return self._inactive_snapshot(
                status="WAITING_MARKET",
                positions=0,
                alert="Waiting for the first screened Gate market universe.",
            )

        account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
        balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
        balance = balance_money.as_decimal() if balance_money else self._initial_balance
        positions = self._engine.cache.positions_open()
        unrealized = Decimal(0)
        for position in positions:
            symbol = self._symbol_for_position(position)
            mark = self._last_marks.get(symbol)
            if mark is not None:
                unrealized += position.unrealized_pnl(mark).as_decimal()
        equity = balance + unrealized
        trades = self._restored_trades + sum(
            strategy.trades for strategy in self._strategies.values()
        )
        fees = self._restored_fees + sum(
            (strategy.fees_usdt for strategy in self._strategies.values()), Decimal(0)
        )
        candidates = self._candidate_snapshots()
        active_symbol = (
            self._symbol_for_position(positions[0])
            if positions
            else self._selected_symbol
            if self._selected_symbol in self._strategies
            else max(
                self._strategies,
                key=lambda symbol: (
                    self._strategies[symbol].confidence,
                    self._strategies[symbol].expected_net_bps,
                    symbol,
                ),
            )
        )
        active_strategy = self._strategies[active_symbol]
        armed = self._entry_enabled and self._settings.strategy_enabled and not self._risk_halted
        scope = self._scope.snapshot(len(positions))
        xau = self._xau_engine.last_decision.as_dict()
        xau["contract_status"] = "VALID" if self._xau_contract_valid else "INVALID"
        xau["contract_reason"] = self._xau_contract_reason
        xau["risk_per_trade_usdt"] = float(self._settings.xau.risk_per_trade_usdt)
        xau["maximum_position_notional_usdt"] = float(
            self._settings.xau.maximum_position_notional_usdt
        )
        xau["leverage"] = float(self._settings.xau.maximum_leverage)
        alerts = []
        if self._risk_halted:
            reason = self._risk_halt_reason or "UNKNOWN"
            if reason == "STRATEGY_EVIDENCE_FAILED":
                alerts.append(
                    "The active PAPER strategy failed the configured evidence gate; "
                    "new entries remain halted."
                )
            else:
                alerts.append(f"Paper risk limit reached ({reason}); new entries are halted.")
        elif not armed:
            alerts.append("REST Momentum tournament is paused.")
        elif any(
            strategy.status.startswith("WARMING_UP") for strategy in self._strategies.values()
        ):
            alerts.append(
                "REST Momentum is monitoring all screened pairs and warming quote windows."
            )
        elif any(
            strategy.status == "WAITING_CONFIRMATION" for strategy in self._strategies.values()
        ):
            alerts.append("REST Momentum is waiting for persistent trend and regime confirmation.")
        elif not positions:
            alerts.append(
                "All screened pairs are monitored; one highest-confidence eligible pair may trade."
            )
        return PaperSnapshot(
            portfolio=self._portfolio(
                equity,
                fees,
                trades,
                len(positions),
            ),
            strategy={
                "name": (
                    "XAU Trend v1"
                    if scope["active_scope"] == MarketScope.XAU_ONLY.value
                    else "REST Momentum Tournament"
                ),
                "symbol": active_symbol,
                "status": active_strategy.status if armed or positions else "PAUSED",
                "armed": armed,
                "confidence": round(active_strategy.confidence, 4),
                "expected_gross_bps": round(active_strategy.expected_gross_bps, 3),
                "expected_cost_bps": round(active_strategy.expected_cost_bps, 3),
                "expected_net_bps": round(active_strategy.expected_net_bps, 3),
                "last_signal": active_strategy.last_signal,
                "monitored_symbols": len(self._strategies),
                "execution_slots": 1,
                "candidates": candidates,
                "market_scope": scope,
                "xau": xau,
                "ama_control": self._ama_snapshot(),
            },
            orders=self._engine.cache.orders_total_count(),
            positions=len(positions),
            risk_halted=self._risk_halted,
            alerts=alerts,
            open_trade=self._open_trade(positions[0] if positions else None),
            trade_history=self._recent_trade_history(),
            diagnostics=self._diagnostics(equity, trades, fees),
        )

    def close(self) -> None:
        self._advance_active_clock()
        self._active_state = False
        if self._engine is not None:
            self._engine.end()
            self._engine.dispose()
            self._engine = None
            self._engine_verified = False

    def _initialize(self, markets: list[dict[str, object]]) -> None:
        self._settings.log_directory.mkdir(parents=True, exist_ok=True)
        logging_config = LoggingConfig(
            log_level=self._settings.log_console_level,
            log_level_file=self._settings.log_file_level,
            log_directory=str(self._settings.log_directory),
            log_file_name="paper-engine",
            log_file_format="json",
            log_file_max_size=self._settings.log_file_max_size,
            log_file_max_backup_count=self._settings.log_file_max_backup_count,
        )
        engine = BacktestEngine(
            BacktestEngineConfig(
                trader_id=TraderId(self._settings.trader_id),
                logging=logging_config,
                risk_engine=RiskEngineConfig(bypass=False),
                run_analysis=False,
            )
        )
        venue = Venue(self._settings.venue)
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=usdt,
            starting_balances=[Money(self._initial_balance, usdt)],
            default_leverage=self._settings.leverage,
        )
        initialized: list[str] = []
        for market in markets:
            raw_symbol = str(market["symbol"])
            if raw_symbol in self._strategies:
                continue
            try:
                is_xau = raw_symbol == self._settings.xau.execution_symbol
                price_increment = _state_decimal(
                    market.get("price_increment"), f"{raw_symbol}.price_increment"
                )
                precision = _decimal_places(price_increment)
                if price_increment <= 0 or precision > 16:
                    raise QuoteValidationError(f"invalid Gate price increment for {raw_symbol}")
                market_ask = _state_decimal(market.get("ask"), f"{raw_symbol}.ask")
                size_increment = Decimal("0.000001")
                minimum_quantity = size_increment
                notional = self._settings.strategy_notional_usdt
                if is_xau:
                    if market.get("contract_enabled") is not True:
                        raise QuoteValidationError(
                            "Gate XAU_USDT contract is disabled or delisting"
                        )
                    leverage_min = _state_decimal(
                        market.get("leverage_min"), "XAU_USDT.leverage_min"
                    )
                    leverage_max = _state_decimal(
                        market.get("leverage_max"), "XAU_USDT.leverage_max"
                    )
                    if not leverage_min <= self._settings.xau.maximum_leverage <= leverage_max:
                        raise QuoteValidationError("configured XAU leverage violates Gate limits")
                    size_increment = _state_decimal(
                        market.get("quantity_increment"), "XAU_USDT.quantity_increment"
                    )
                    minimum_quantity = _state_decimal(
                        market.get("minimum_quantity"), "XAU_USDT.minimum_quantity"
                    )
                    if size_increment <= 0 or minimum_quantity <= 0:
                        raise QuoteValidationError("invalid Gate XAU quantity metadata")
                    notional = self._settings.xau.maximum_position_notional_usdt
                    self._xau_contract_valid = True
                    self._xau_contract_reason = "VALID"
                trade_size = (notional / market_ask / size_increment).to_integral_value(
                    rounding=ROUND_DOWN
                ) * size_increment
                if trade_size <= 0:
                    raise QuoteValidationError(
                        f"paper trade quantity rounded to zero for {raw_symbol}"
                    )
            except (InvalidOperation, ValueError, QuoteValidationError) as exc:
                self._symbol_errors[raw_symbol] = f"PRECISION_REJECTED: {exc}"[:160]
                if raw_symbol == self._settings.xau.execution_symbol:
                    self._xau_contract_valid = False
                    self._xau_contract_reason = str(exc)[:160]
                self._logger.warning("Paper symbol rejected %s: %s", raw_symbol, exc)
                continue
            base = Currency.from_str(raw_symbol.removesuffix("_USDT"))
            timestamp = time_ns()
            instrument_id = InstrumentId(Symbol(f"{raw_symbol}-PERP"), venue)
            instrument = CryptoPerpetual(
                instrument_id=instrument_id,
                raw_symbol=Symbol(raw_symbol),
                base_currency=base,
                quote_currency=usdt,
                settlement_currency=usdt,
                is_inverse=False,
                price_precision=precision,
                size_precision=_decimal_places(size_increment),
                price_increment=Price.from_str(format(price_increment, "f")),
                size_increment=Quantity.from_str(format(size_increment, "f")),
                ts_event=timestamp,
                ts_init=timestamp,
                margin_init=Decimal("1"),
                margin_maint=Decimal("1"),
                maker_fee=Decimal("0.0002"),
                taker_fee=TAKER_FEE,
            )
            strategy = RestMomentumStrategy(
                MomentumConfig(
                    instrument_id=instrument_id,
                    trade_size=trade_size,
                    size_precision=_decimal_places(size_increment),
                    size_increment=size_increment,
                    window=(
                        self._settings.xau.short_window
                        if is_xau
                        else self._settings.strategy_window
                    ),
                    entry_threshold_bps=float(
                        self._settings.xau.entry_threshold_bps
                        if is_xau
                        else self._settings.strategy_entry_threshold_bps
                    ),
                    minimum_net_edge_bps=float(
                        self._settings.strategy_minimum_net_edge_bps
                    ),
                    minimum_confidence=float(
                        self._settings.xau.minimum_confidence
                        if is_xau
                        else self._settings.strategy_minimum_confidence
                    ),
                    stop_loss_bps=float(
                        self._settings.xau.stop_loss_bps
                        if is_xau
                        else self._settings.strategy_stop_loss_bps
                    ),
                    take_profit_bps=float(
                        self._settings.xau.take_profit_bps
                        if is_xau
                        else self._settings.strategy_take_profit_bps
                    ),
                    persistence_ticks=(
                        min(
                            self._settings.strategy_persistence_ticks,
                            self._settings.xau.short_window,
                        )
                        if is_xau
                        else self._settings.strategy_persistence_ticks
                    ),
                    regime_window=(
                        self._settings.xau.trend_window
                        if is_xau
                        else self._settings.strategy_regime_window
                    ),
                    max_hold_ns=(
                        self._settings.xau.maximum_holding_seconds
                        if is_xau
                        else self._settings.strategy_max_hold_seconds
                    )
                    * 1_000_000_000,
                    cooldown_ns=(
                        self._settings.xau.cooldown_seconds
                        if is_xau
                        else self._settings.strategy_cooldown_seconds
                    )
                    * 1_000_000_000,
                    signal_reset_reentry=self._settings.experiments.signal_reset_reentry,
                    exit_variant=(
                        "TP75_RUNNER25"
                        if is_xau and self._settings.xau.trailing_enabled
                        else "BASELINE_FULL_TP"
                        if is_xau
                        else self._settings.experiments.exit_variant
                    ),
                    runner_trail_bps=float(
                        self._settings.xau.trailing_bps
                        if is_xau
                        else self._settings.experiments.runner_trail_bps
                    ),
                    runner_max_hold_ns=(
                        (
                            self._settings.xau.maximum_holding_seconds
                            if is_xau
                            else self._settings.experiments.runner_max_hold_seconds
                        )
                        * 1_000_000_000
                    ),
                    edge_decay_time_exit=(
                        False
                        if is_xau
                        else self._settings.experiments.edge_decay_time_exit
                    ),
                )
            )
            engine.add_instrument(instrument)
            engine.add_strategy(strategy)
            self._strategies[raw_symbol] = strategy
            self._instruments[raw_symbol] = instrument
            initialized.append(raw_symbol)
        if not initialized:
            raise RuntimeError("paper universe has no valid instruments")
        self._engine = engine
        self._logger.info(
            "Paper tournament initialized symbols=%s execution_slots=1 notional=%s leverage=%s",
            ",".join(initialized),
            self._settings.strategy_notional_usdt,
            self._settings.leverage,
        )

    def _verify_engine_accounting(self) -> None:
        if self._engine is None:
            raise RuntimeError("paper engine initialization failed")
        account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
        balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
        if balance_money is None:
            raise RuntimeError("initialized paper account has no USDT balance")
        engine_balance = balance_money.as_decimal()
        if abs(engine_balance - self._initial_balance) > ACCOUNTING_TOLERANCE:
            reason = "checkpoint/Nautilus opening balance mismatch"
            self._emit_reconciliation_failure(reason, engine_balance, self._initial_balance)
            self._set_state_invalid(reason)
            raise RuntimeError(reason)

    def _emit_reconciliation_failure(
        self,
        reason: str,
        engine_equity: Decimal,
        ledger_equity: Decimal,
    ) -> None:
        if self._ledger is None:
            self._logger.error("Paper reconciliation failed: %s", reason)
            return
        self._pending_event_specs.append(
            (
                "state_reconciliation_failed",
                {
                    "reason": reason,
                    "checkpoint_equity": str(self._initial_balance),
                    "engine_equity": str(engine_equity),
                    "ledger_equity": str(ledger_equity),
                    "difference": str(engine_equity - ledger_equity),
                },
            )
        )

    def _quote(self, symbol: str, market: dict[str, object]) -> QuoteTick:
        instrument = self._instruments[symbol]
        raw_bid = _state_decimal(market.get("bid"), f"{symbol}.bid")
        raw_ask = _state_decimal(market.get("ask"), f"{symbol}.ask")
        increment = instrument.price_increment.as_decimal()
        bid, ask = construct_pessimistic_prices(
            raw_bid,
            raw_ask,
            increment,
            (
                self._settings.xau.slippage_bps
                if symbol == self._settings.xau.execution_symbol
                else self._settings.strategy_slippage_bps
            ),
        )
        self._strategies[symbol].set_market_context(
            raw_bid=raw_bid,
            raw_ask=raw_ask,
            modeled_bid=bid,
            modeled_ask=ask,
        )
        precision = instrument.price_precision
        bid_price = Price.from_str(f"{bid:.{precision}f}")
        ask_price = Price.from_str(f"{ask:.{precision}f}")
        timestamp = max(time_ns(), self._last_ts + 1)
        self._last_ts = timestamp
        mark = ((bid + ask) / 2 / increment).to_integral_value(rounding=ROUND_FLOOR) * increment
        self._last_marks[symbol] = Price.from_str(f"{mark:.{precision}f}")
        return QuoteTick(
            instrument_id=instrument.id,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_size=Quantity(Decimal(100), instrument.size_precision),
            ask_size=Quantity(Decimal(100), instrument.size_precision),
            ts_event=timestamp,
            ts_init=timestamp,
        )

    def _candidate_snapshots(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": symbol,
                "status": strategy.status,
                "selected": bool(self._last_markets.get(symbol, {}).get("selected")),
                "eligible": strategy.entry_candidate,
                "confidence": round(strategy.confidence, 4),
                "expected_gross_bps": round(strategy.expected_gross_bps, 3),
                "expected_cost_bps": round(strategy.expected_cost_bps, 3),
                "expected_net_bps": round(strategy.expected_net_bps, 3),
                "last_signal": strategy.last_signal,
                "blocked_reason": strategy.blocked_reason,
            }
            for symbol, strategy in sorted(
                self._strategies.items(),
                key=lambda item: (
                    item[1].confidence,
                    item[1].expected_net_bps,
                    item[0],
                ),
                reverse=True,
            )
        ]

    def _entry_sizing(
        self,
        entry_price: Decimal,
        *,
        symbol: str,
        size_factor: Decimal = Decimal(1),
    ) -> SizingResult:
        if symbol == self._settings.xau.execution_symbol:
            market = self._last_markets.get(symbol, {})
            increment = _state_decimal(
                market.get("quantity_increment", "0.0001"),
                "XAU_USDT.quantity_increment",
            )
            minimum = _state_decimal(
                market.get("minimum_quantity", increment),
                "XAU_USDT.minimum_quantity",
            )
            return risk_adjusted_quantity(
                entry_price=entry_price,
                stop_distance_bps=self._settings.xau.stop_loss_bps,
                risk_budget_usdt=self._settings.xau.risk_per_trade_usdt * size_factor,
                fee_buffer_bps=Decimal("10"),
                slippage_buffer_bps=self._settings.xau.slippage_bps * 2,
                maximum_position_notional_usdt=(
                    self._settings.xau.maximum_position_notional_usdt * size_factor
                ),
                maximum_symbol_exposure_usdt=(
                    self._settings.xau.maximum_position_notional_usdt * size_factor
                ),
                liquidity_cap_usdt=(
                    self._settings.xau.maximum_position_notional_usdt * size_factor
                ),
                size_increment=increment,
                minimum_quantity=minimum,
            )
        experiment = self._settings.experiments
        if experiment.risk_normalized_sizing:
            return risk_adjusted_quantity(
                entry_price=entry_price,
                stop_distance_bps=self._settings.strategy_stop_loss_bps,
                risk_budget_usdt=experiment.risk_budget_usdt,
                fee_buffer_bps=Decimal("10"),
                slippage_buffer_bps=self._settings.strategy_slippage_bps * 2,
                maximum_position_notional_usdt=experiment.maximum_position_notional_usdt,
                maximum_symbol_exposure_usdt=experiment.maximum_symbol_exposure_usdt,
                liquidity_cap_usdt=experiment.liquidity_notional_cap_usdt,
                size_increment=Decimal("0.000001"),
                minimum_quantity=Decimal("0.000001"),
            )
        return capped_notional_quantity(
            entry_price=entry_price,
            notional_cap_usdt=self._settings.strategy_notional_usdt,
            size_increment=Decimal("0.000001"),
        )

    def _entry_guard_reason(
        self,
        symbol: str,
        strategy: RestMomentumStrategy,
        market: dict[str, object],
    ) -> str | None:
        if self._report.mode != "PAPER" or self._settings.live_trading_enabled:
            return "PAPER_PERMISSION"
        if not self._entry_enabled or self._risk_halted:
            return "TRADING_DISABLED"
        entry_scope = self._scope.entry_scope
        if entry_scope is None:
            return "MARKET_SCOPE_TRANSITION"
        is_xau = symbol == self._settings.xau.execution_symbol
        if (entry_scope == MarketScope.XAU_ONLY) != is_xau:
            return "MARKET_SCOPE"
        if entry_scope == MarketScope.WIDE_CRYPTO and symbol in self._scope.required_symbols:
            return "MARKET_SCOPE"
        updated = self._last_market_monotonic.get(symbol)
        stale_after = (
            self._settings.xau.stale_after_seconds
            if is_xau
            else self._settings.market_stale_after_seconds
        )
        if updated is None or monotonic() - updated > stale_after:
            return "STALE_DATA"
        if not is_xau and not bool(market.get("selected")):
            return "UNIVERSE"
        raw_spread = market.get("spread_bps")
        if raw_spread is None:
            bid = _state_decimal(market.get("bid"), f"{symbol}.bid")
            ask = _state_decimal(market.get("ask"), f"{symbol}.ask")
            raw_spread = (ask - bid) / ((ask + bid) / 2) * Decimal(10_000)
        if Decimal(str(raw_spread)) > (
            self._settings.xau.maximum_spread_bps
            if is_xau
            else self._settings.maximum_spread_bps
        ):
            return "SPREAD_LIMIT"
        if is_xau:
            decision = self._xau_engine.last_decision
            if not self._xau_contract_valid:
                return "XAU_CONTRACT_INVALID"
            if decision.direction == XauDirection.WAIT:
                return "XAU_WAIT"
            if decision.confidence < self._settings.xau.minimum_confidence:
                return "LOW_CONFIDENCE"
            xau_trades = [
                trade
                for trade in self._trade_history
                if trade.get("market_class") == "xau" or trade.get("symbol") == symbol
            ]
            utc_day = datetime.now(UTC).date().isoformat()
            daily_loss = sum(
                (
                    Decimal(str(trade.get("realized_pnl_usdt", 0)))
                    for trade in xau_trades
                    if str(trade.get("closed_at", "")).startswith(utc_day)
                    and Decimal(str(trade.get("realized_pnl_usdt", 0))) < 0
                ),
                Decimal(0),
            )
            if -daily_loss >= self._settings.xau.maximum_daily_loss_usdt:
                return "XAU_DAILY_LOSS"
            consecutive_losses = 0
            for trade in reversed(xau_trades):
                if Decimal(str(trade.get("realized_pnl_usdt", 0))) >= 0:
                    break
                consecutive_losses += 1
            if consecutive_losses >= self._settings.xau.maximum_consecutive_losses:
                return "XAU_CONSECUTIVE_LOSSES"
        if strategy.has_position or strategy.has_pending_order:
            return "DUPLICATE_POSITION"
        return None

    def _rank_candidates(
        self,
        candidates: list[tuple[str, RestMomentumStrategy]],
        markets: dict[str, dict[str, object]],
    ) -> list[tuple[str, RestMomentumStrategy]]:
        if not self._settings.experiments.candidate_ranking:
            return sorted(
                candidates,
                key=lambda item: (item[1].confidence, item[1].expected_net_bps, item[0]),
                reverse=True,
            )
        ranked_values = deterministic_candidate_ranking(
            {
                "symbol": symbol,
                "confidence": strategy.confidence,
                "expected_net_bps": strategy.expected_net_bps,
                "spread_bps": markets[symbol].get("spread_bps", 1_000_000),
            }
            for symbol, strategy in candidates
        )
        by_symbol = dict(candidates)
        return [(str(item["symbol"]), by_symbol[str(item["symbol"])]) for item in ranked_values]

    def _experimental_block_reason(
        self,
        symbol: str,
        strategy: RestMomentumStrategy,
        market: dict[str, object],
    ) -> str | None:
        experiment = self._settings.experiments
        if experiment.cost_to_edge_gate:
            reason = cost_gate_reason(
                Decimal(str(strategy.expected_gross_bps)),
                Decimal(str(strategy.expected_cost_bps)),
                experiment.cost_multiplier,
            )
            if reason is not None:
                return reason
        if experiment.market_quality_filters:
            reasons = market_quality_reasons(
                market,
                maximum_spread_bps=experiment.maximum_entry_spread_bps,
                minimum_quote_volume=experiment.minimum_entry_quote_volume,
                minimum_depth_usdt=experiment.minimum_depth_usdt,
                maximum_one_bar_volatility_bps=experiment.maximum_one_bar_volatility_bps,
                maximum_price_gap_bps=experiment.maximum_price_gap_bps,
                require_order_book=experiment.require_order_book,
                stale_after_seconds=Decimal(self._settings.market_stale_after_seconds),
            )
            if reasons:
                return reasons[0]
        if any(
            (
                experiment.signal_reset_reentry,
                experiment.maximum_symbol_attempts > 0,
                experiment.maximum_consecutive_symbol_losses > 0,
                experiment.maximum_symbol_loss_utc_day_usdt > 0,
                experiment.maximum_symbol_loss_wib_day_usdt > 0,
            )
        ):
            return churn_block_reason(
                self._trade_history,
                symbol=symbol,
                now_utc=utc_now(),
                signal_reset_required=experiment.signal_reset_reentry,
                signal_has_reset=strategy.signal_has_reset,
                maximum_attempts=experiment.maximum_symbol_attempts,
                attempt_window_seconds=experiment.attempt_window_seconds,
                maximum_consecutive_losses=experiment.maximum_consecutive_symbol_losses,
                maximum_utc_day_loss_usdt=experiment.maximum_symbol_loss_utc_day_usdt,
                maximum_wib_day_loss_usdt=experiment.maximum_symbol_loss_wib_day_usdt,
            )
        return None

    def _symbol_for_position(self, position: object) -> str:
        instrument_id = position.instrument_id  # type: ignore[attr-defined]
        for symbol, instrument in self._instruments.items():
            if instrument.id == instrument_id:
                return symbol
        raise RuntimeError(f"paper position instrument is not monitored: {instrument_id}")

    def _apply_risk_limits(self) -> None:
        _, equity, trades, _, _ = self._accounting_totals()
        self._roll_risk_day(equity, trades)
        pnl = equity - self._day_start_equity
        drawdown = (
            (self._peak_equity - equity) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        if pnl <= -self._settings.strategy_daily_loss_usdt:
            self._risk_halted = True
            if self._risk_halt_reason in {None, "STRATEGY_EVIDENCE_FAILED"}:
                self._risk_halt_reason = "DAILY_LOSS"
        if drawdown >= self._settings.strategy_max_drawdown_pct:
            self._risk_halted = True
            if self._risk_halt_reason in {None, "STRATEGY_EVIDENCE_FAILED"}:
                self._risk_halt_reason = "MAX_DRAWDOWN"
        self._apply_strategy_evidence_halt()
        if self._risk_halted:
            self._entry_enabled = False
            for strategy in self._strategies.values():
                strategy.entry_enabled = False
                if (
                    strategy.has_position
                    and self._risk_halt_reason != "STRATEGY_EVIDENCE_FAILED"
                ):
                    strategy.request_flatten("RISK_FLATTEN")

    def _apply_strategy_evidence_halt(self) -> None:
        if self._strategy_evidence_status()["status"] != "FAILED":
            return
        self._risk_halted = True
        self._risk_halt_reason = self._risk_halt_reason or "STRATEGY_EVIDENCE_FAILED"

    def _strategy_evidence_status(self) -> dict[str, object]:
        normal = [
            trade
            for trade in self._analytics_trades
            if trade.get("classification") == "NORMAL"
        ]
        trading_days = {
            str(trade.get("closed_at", ""))[:10]
            for trade in normal
            if len(str(trade.get("closed_at", ""))) >= 10
        }
        result: dict[str, object] = {
            "status": (
                "DISABLED"
                if not self._settings.strategy_evidence_halt_enabled
                else "COLLECTING"
            ),
            "normal_trades": len(normal),
            "trading_days": len(trading_days),
            "minimum_trades": self._settings.strategy_evidence_minimum_trades,
            "minimum_trading_days": self._settings.strategy_evidence_minimum_trading_days,
            "profit_factor_floor": float(
                self._settings.strategy_evidence_profit_factor_floor
            ),
            "method": "BOTH_CHRONOLOGICAL_HALVES",
        }
        if not self._settings.strategy_evidence_halt_enabled:
            return result
        if (
            len(normal) < self._settings.strategy_evidence_minimum_trades
            or len(trading_days) < self._settings.strategy_evidence_minimum_trading_days
        ):
            return result

        def window_metrics(trades: list[dict[str, object]]) -> dict[str, object]:
            outcomes = [Decimal(str(trade.get("net_pnl_usdt", 0))) for trade in trades]
            gross_profit = sum((max(outcome, Decimal(0)) for outcome in outcomes), Decimal(0))
            gross_loss = -sum((min(outcome, Decimal(0)) for outcome in outcomes), Decimal(0))
            profit_factor = gross_profit / gross_loss if gross_loss else None
            return {
                "trades": len(trades),
                "net_pnl_usdt": float(sum(outcomes, Decimal(0))),
                "profit_factor": float(profit_factor) if profit_factor is not None else None,
            }

        midpoint = len(normal) // 2
        first = window_metrics(normal[:midpoint])
        second = window_metrics(normal[midpoint:])
        floor = self._settings.strategy_evidence_profit_factor_floor

        def failed_window(window: dict[str, object]) -> bool:
            profit_factor = window["profit_factor"]
            return (
                Decimal(str(window["net_pnl_usdt"])) < 0
                and profit_factor is not None
                and Decimal(str(profit_factor)) < floor
            )

        failed = all(failed_window(window) for window in (first, second))
        result["status"] = "FAILED" if failed else "PASS"
        result["first_half"] = first
        result["second_half"] = second
        return result

    def _roll_risk_day(self, equity: Decimal, trades: int) -> None:
        today = datetime.now(UTC).date().isoformat()
        if today == self._risk_day_utc:
            self._peak_equity = max(self._peak_equity, equity)
            return
        self._persist_state(rollover=False)
        prior_daily_halt = (
            self._risk_halt_reason
            if self._risk_halted and self._risk_halt_reason in {"DAILY_LOSS", "MAX_DRAWDOWN"}
            else None
        )
        self._risk_day_utc = today
        self._day_start_equity = equity
        self._trades_at_day_start = trades
        self._peak_equity = equity
        if prior_daily_halt:
            self._risk_halted = True
            self._risk_halt_reason = f"{prior_daily_halt}_REVIEW"
            self._rollover_review_required = True
        self._persist_state(rollover=False)

    def _inactive_snapshot(self, *, status: str, positions: int, alert: str) -> PaperSnapshot:
        alerts = [alert]
        if self._risk_halted:
            reason = self._risk_halt_reason or "UNKNOWN"
            alert = (
                "The active PAPER strategy failed the configured evidence gate; "
                "new entries remain halted."
                if reason == "STRATEGY_EVIDENCE_FAILED"
                else f"Paper risk limit reached ({reason}); new entries are halted."
            )
            alerts.insert(0, alert)
        return PaperSnapshot(
            portfolio=self._portfolio(
                self._initial_balance,
                self._restored_fees,
                self._restored_trades,
                positions,
            ),
            strategy={
                "name": "REST Momentum Tournament",
                "symbol": str(
                    (self._recovery_position or {}).get("symbol", self._settings.strategy_symbol)
                ),
                "status": status,
                "armed": False,
                "confidence": 0.0,
                "expected_gross_bps": 0.0,
                "expected_cost_bps": 0.0,
                "expected_net_bps": 0.0,
                "last_signal": None,
                "monitored_symbols": len(self._strategies),
                "execution_slots": 1,
                "candidates": self._candidate_snapshots(),
                "ama_control": self._ama_snapshot(),
            },
            orders=0,
            positions=positions,
            risk_halted=self._risk_halted or self._state_error is not None,
            alerts=alerts,
            open_trade=self._open_trade(None),
            trade_history=self._recent_trade_history(),
            diagnostics=self._diagnostics(
                self._initial_balance,
                self._restored_trades,
                self._restored_fees,
            ),
        )

    def _diagnostics(
        self,
        equity: Decimal,
        trades: int,
        fees: Decimal,
    ) -> dict[str, Mapping[str, object]]:
        usage = shutil.disk_usage(self._settings.log_directory.parent)
        free_percent = usage.free / usage.total * 100 if usage.total else 0.0
        last_event_sequence = self._ledger.last_sequence if self._ledger is not None else None
        return {
            "accounting": {
                "state": self._accounting_state,
                "reason": self._accounting_error,
                "equity_usdt": str(equity),
                "starting_equity_usdt": str(self._starting_equity),
                "cumulative_realized_pnl_usdt": str(self._cumulative_realized_pnl),
                "fees_usdt": str(fees),
                "trade_count": trades,
                "checkpoint_sequence": self._checkpoint_sequence,
                "last_event_sequence": last_event_sequence,
                "tolerance_usdt": str(ACCOUNTING_TOLERANCE),
            },
            "run": {
                **self._metadata,
                "run_id": self._run_id,
                "session_id": self._session_id,
                "event_schema_version": EVENT_SCHEMA_VERSION,
            },
            "risk": {
                "state": self._risk_halt_reason or "OK",
                "risk_day_utc": self._risk_day_utc,
                "day_start_equity_usdt": str(self._day_start_equity),
                "day_peak_equity_usdt": str(self._peak_equity),
                "drawdown_window": "UTC_DAY",
                "rollover_review_required": self._rollover_review_required,
                "xau_risk_per_trade_usdt": float(self._settings.xau.risk_per_trade_usdt),
                "xau_maximum_position_notional_usdt": float(
                    self._settings.xau.maximum_position_notional_usdt
                ),
                "xau_leverage": float(self._settings.xau.maximum_leverage),
            },
            "strategy_evidence": self._strategy_evidence_status(),
            "execution_model": {
                "state": "PAPER_SIM",
                "version": EXECUTION_MODEL_VERSION,
                "market_data_mode": MARKET_DATA_MODE,
                "spread": "OBSERVED",
                "fees": "MODELED",
                "slippage": "MODELED",
                "decision_latency": "NOT_MODELED",
                "submit_latency": "NOT_MODELED",
                "fill_latency": "NOT_MODELED",
                "partial_fills": "OBSERVED",
                "fill_ratio": "NOT_MODELED",
                "adverse_selection": "NOT_MODELED",
                "funding": "NOT_MODELED",
                "queue_model": "NOT_MODELED",
            },
            "profitability": self._profitability_diagnostics(equity),
            "recovery_timing": {
                "last_successful_heartbeat_utc": self._last_successful_heartbeat_utc,
                "outage_started_at_utc": self._outage_started_at_utc,
                "restart_detected_at_utc": self._restart_detected_at_utc,
                "outage_duration_ms": self._outage_duration_ms,
                "position_exposed_during_outage": self._position_exposed_during_outage,
                "recovery_action": self._recovery_action,
                "recovery_completed_at_utc": self._recovery_completed_at_utc,
            },
            "experiments": self._experiment_diagnostics(),
            "ama_control": self._ama_snapshot(),
            "symbols": {
                "quarantined": dict(sorted(self._symbol_errors.items())),
            },
            "storage": {
                "free_disk_bytes": usage.free,
                "free_disk_percent": round(free_percent, 2),
                "safe": usage.free >= MIN_FREE_DISK_BYTES,
            },
        }

    def _ama_snapshot(self) -> dict[str, object]:
        self._ama_control.set_external_benchmark(
            "CURRENT_PAPER_BASELINE", self._paper_baseline_benchmark()
        )
        snapshot = self._ama_control.snapshot()
        if self._ama_control_error is not None:
            snapshot["status"] = "ERROR"
            snapshot["error"] = self._ama_control_error
        return snapshot

    def _paper_baseline_benchmark(self) -> dict[str, object]:
        aggregate = aggregate_trades(
            self._analytics_trades,
            starting_equity=self._starting_equity,
        )
        normal = aggregate.get("normal", {})
        return {
            **(normal if isinstance(normal, dict) else {}),
            "status": "RUN_LEDGER_NOT_SAME_TIMELINE",
            "max_drawdown_usdt": aggregate.get("normal_strategy_max_drawdown_usdt"),
            "sample_status": "NOT_COMPARABLE",
        }

    def _load_analytics_trades(self) -> list[dict[str, object]]:
        if not self._events_path.is_file():
            return []
        events: list[dict[str, object]] = []
        try:
            with self._events_path.open(encoding="utf-8") as file:
                for line in file:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.append(event)
            return reconstruct_trades(events)
        except (OSError, ValueError, json.JSONDecodeError):
            return []

    def _profitability_diagnostics(self, equity: Decimal) -> dict[str, object]:
        active_hours = (
            self._strategy_active_seconds / Decimal(3600)
            if self._strategy_active_seconds > 0
            else None
        )
        aggregate = aggregate_trades(
            self._analytics_trades,
            starting_equity=self._starting_equity,
            active_hours=active_hours,
        )
        self._session_peak_equity = max(self._session_peak_equity, equity)
        session_drawdown = (
            (self._session_peak_equity - equity) / self._session_peak_equity * 100
            if self._session_peak_equity
            else Decimal(0)
        )
        run_high = self._starting_equity
        running = self._starting_equity
        normal_running = self._starting_equity
        normal_high = self._starting_equity
        for trade in self._analytics_trades:
            pnl = Decimal(str(trade.get("net_pnl_usdt", 0)))
            running += pnl
            run_high = max(run_high, running)
            if trade.get("classification") == "NORMAL":
                normal_running += pnl
                normal_high = max(normal_high, normal_running)
        run_start_drawdown = (
            max(Decimal(0), self._starting_equity - equity) / self._starting_equity * 100
            if self._starting_equity
            else Decimal(0)
        )
        all_time_drawdown = (
            max(Decimal(0), run_high - equity) / run_high * 100 if run_high else Decimal(0)
        )
        normal_drawdown = (
            max(Decimal(0), normal_high - normal_running) / normal_high * 100
            if normal_high
            else Decimal(0)
        )
        wib_day = datetime.now(UTC).astimezone(WIB).date()
        wib_trades = [
            trade
            for trade in self._analytics_trades
            if datetime.fromisoformat(str(trade["closed_at_wib"])).date() == wib_day
        ]
        wib_pnl = sum((Decimal(str(trade["net_pnl_usdt"])) for trade in wib_trades), Decimal(0))
        wib_start = equity - wib_pnl
        wib_peak = wib_start
        wib_running = wib_start
        for trade in wib_trades:
            wib_running += Decimal(str(trade["net_pnl_usdt"]))
            wib_peak = max(wib_peak, wib_running)
        wib_drawdown = (
            max(Decimal(0), wib_peak - equity) / wib_peak * 100 if wib_peak else Decimal(0)
        )
        day_drawdown = (
            max(Decimal(0), self._peak_equity - equity) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        open_entry_fee = sum(
            (strategy.entry_fee_usdt for strategy in self._strategies.values()), Decimal(0)
        )
        open_risk = Decimal(0)
        for symbol, strategy in self._strategies.items():
            if strategy.has_position:
                stop_bps = (
                    self._settings.xau.stop_loss_bps
                    if symbol == self._settings.xau.execution_symbol
                    else self._settings.strategy_stop_loss_bps
                )
                slippage_bps = (
                    self._settings.xau.slippage_bps
                    if symbol == self._settings.xau.execution_symbol
                    else self._settings.strategy_slippage_bps
                )
                open_risk += (
                    strategy._entry_notional
                    * (stop_bps + Decimal("10") + slippage_bps * 2)
                    / Decimal(10_000)
                )
        today = datetime.now(UTC).date().isoformat()
        daily_risk_usage = -sum(
            (
                min(Decimal(str(trade["net_pnl_usdt"])), Decimal(0))
                for trade in self._analytics_trades
                if trade.get("classification") == "NORMAL"
                and str(trade.get("closed_at", "")).startswith(today)
            ),
            Decimal(0),
        )
        unrealized = Decimal(0)
        if self._engine is not None:
            account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
            balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
            if balance_money is not None:
                unrealized = equity - balance_money.as_decimal()
        market_classes = aggregate.get("market_classes")
        if isinstance(market_classes, dict):
            all_summary = market_classes.get("ALL")
            if isinstance(all_summary, dict):
                all_summary["unrealized_pnl_usdt"] = float(unrealized)
            open_scope = None
            if self._engine is not None:
                positions = self._engine.cache.positions_open()
                if positions:
                    open_scope = (
                        "XAU"
                        if self._symbol_for_position(positions[0])
                        == self._settings.xau.execution_symbol
                        else "CRYPTO"
                    )
            for scope_name in ("CRYPTO", "XAU"):
                summary = market_classes.get(scope_name)
                if isinstance(summary, dict):
                    summary["unrealized_pnl_usdt"] = (
                        float(unrealized) if open_scope == scope_name else 0.0
                    )
        return {
            **aggregate,
            "current_session_drawdown_pct": float(session_drawdown),
            "current_utc_day_drawdown_pct": float(day_drawdown),
            "current_wib_day_drawdown_pct": float(wib_drawdown),
            "current_run_start_drawdown_pct": float(run_start_drawdown),
            "current_all_time_high_drawdown_pct": float(all_time_drawdown),
            "current_normal_strategy_drawdown_pct": float(normal_drawdown),
            "current_total_accounting_drawdown_pct": float(all_time_drawdown),
            "current_open_risk_usdt": float(open_risk),
            "realized_daily_risk_usage_usdt": float(daily_risk_usage),
            "open_position_fees_usdt": float(open_entry_fee),
            "realized_pnl_usdt": float(self._cumulative_realized_pnl),
            "unrealized_pnl_usdt": float(unrealized),
        }

    def _experiment_diagnostics(self) -> dict[str, object]:
        experiment = self._settings.experiments
        normal_count = sum(
            1 for trade in self._analytics_trades if trade.get("classification") == "NORMAL"
        )
        return {
            "mode": "PAPER_EXPERIMENT"
            if any(
                (
                    experiment.risk_normalized_sizing,
                    experiment.signal_reset_reentry,
                    experiment.candidate_ranking,
                    experiment.cost_to_edge_gate,
                    experiment.market_quality_filters,
                    experiment.exit_variant != "BASELINE_FULL_TP",
                    experiment.edge_decay_time_exit,
                    experiment.protective_orders,
                )
            )
            else "UNCHANGED_BASELINE",
            "risk_normalized_sizing": experiment.risk_normalized_sizing,
            "signal_reset_reentry": experiment.signal_reset_reentry,
            "candidate_ranking": experiment.candidate_ranking,
            "cost_to_edge_gate": experiment.cost_to_edge_gate,
            "market_quality_filters": experiment.market_quality_filters,
            "exit_variant": experiment.exit_variant,
            "edge_decay_time_exit": experiment.edge_decay_time_exit,
            "protective_orders": experiment.protective_orders,
            "protective_order_scope": "PAPER_SIMULATION_ONLY",
            "rejection_counts": dict(sorted(self._rejection_counts.items())),
            "evidence_status": "INSUFFICIENT EVIDENCE" if normal_count < 100 else "UNPROVEN",
            "normal_trade_count": normal_count,
        }

    def _recovery_checkpoint_fields(self) -> dict[str, object]:
        return {
            "last_successful_heartbeat_utc": self._last_successful_heartbeat_utc,
            "outage_started_at_utc": self._outage_started_at_utc,
            "restart_detected_at_utc": self._restart_detected_at_utc,
            "outage_duration_ms": self._outage_duration_ms,
            "position_exposed_during_outage": self._position_exposed_during_outage,
            "recovery_action": self._recovery_action,
            "recovery_completed_at_utc": self._recovery_completed_at_utc,
        }

    def _load_state(self) -> None:
        if not self._state_path.exists():
            has_legacy = (
                self._legacy_events_path.is_file() and self._legacy_events_path.stat().st_size > 0
            )
            has_prior_run = any((self._project_root / "data" / "runs").glob("*/metadata.json"))
            if has_legacy or has_prior_run:
                self._set_state_invalid(
                    "checkpoint is missing while prior paper-run evidence exists"
                )
                return
            try:
                create_new_run(
                    project_root=self._project_root,
                    config_path=Path(__file__).resolve().parents[1] / "config" / "paper.toml",
                    log_directory=self._settings.log_directory,
                    starting_equity=self._settings.starting_balance_usdt,
                )
            except (OSError, ValueError) as exc:
                self._set_state_invalid(f"fresh paper run creation failed: {exc}")
                return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state must be an object")
            schema_version = int(state.get("schema_version", 1))
            if schema_version not in {1, 2, STATE_SCHEMA_VERSION}:
                raise ValueError("unsupported state schema")
            if state.get("mode") != "PAPER":
                raise ValueError("mode mismatch")
            previous_heartbeat = state.get(
                "last_successful_heartbeat_utc", state.get("updated_at_utc")
            )
            if previous_heartbeat is not None:
                self._last_successful_heartbeat_utc = canonical_utc(str(previous_heartbeat))
            if self._checkpoint_existed_at_start and self._last_successful_heartbeat_utc:
                self._outage_started_at_utc = self._last_successful_heartbeat_utc
                if self._restart_detected_at_utc is not None:
                    self._outage_duration_ms = duration_ms(
                        self._outage_started_at_utc,
                        self._restart_detected_at_utc,
                    )
            persisted_completion = state.get("recovery_completed_at_utc")
            if persisted_completion is not None:
                self._recovery_completed_at_utc = canonical_utc(str(persisted_completion))
            persisted_action = state.get("recovery_action")
            if isinstance(persisted_action, str) and persisted_action:
                self._recovery_action = persisted_action
            persisted_exposure = state.get("position_exposed_during_outage", False)
            if isinstance(persisted_exposure, bool):
                self._position_exposed_during_outage = persisted_exposure
            if schema_version == STATE_SCHEMA_VERSION:
                run_id = str(state.get("run_id", ""))
                ledger_path = (
                    self._project_root / Path(str(state.get("event_ledger_path", "")))
                ).resolve()
                if not ledger_path.is_relative_to(self._project_root.resolve()):
                    raise IntegrityError("event ledger path escapes the project root")
                recovered = recover_pending_transition(self._state_path, ledger_path, run_id)
                if recovered is not None:
                    state = recovered
                    self._logger.warning(
                        "Recovered committed paper transition %s",
                        recovered.get("last_transition_id"),
                    )
            self._initial_balance = _state_decimal(state["balance_usdt"], "balance_usdt")
            if self._initial_balance < 0:
                raise ValueError("balance_usdt must be non-negative")
            self._restored_trades = int(state.get("trades", 0))
            if self._restored_trades < 0:
                raise ValueError("trades must be non-negative")
            self._restored_fees = _state_decimal(state.get("fees_usdt", 0), "fees_usdt")
            if self._restored_fees < 0:
                raise ValueError("fees_usdt must be non-negative")
            self._starting_equity = _state_decimal(
                state.get("starting_equity_usdt", self._settings.starting_balance_usdt),
                "starting_equity_usdt",
            )
            self._cumulative_realized_pnl = _state_decimal(
                state.get(
                    "cumulative_realized_pnl_usdt",
                    self._initial_balance - self._starting_equity,
                ),
                "cumulative_realized_pnl_usdt",
            )
            self._strategy_active_seconds = _state_decimal(
                state.get("strategy_active_seconds", 0),
                "strategy_active_seconds",
            )
            if self._strategy_active_seconds < 0:
                raise ValueError("strategy_active_seconds must not be negative")
            self._load_risk_state(state)
            position = state.get("position")
            if position is not None and not isinstance(position, dict):
                raise ValueError("position must be an object")
            if position is not None:
                position.setdefault("symbol", state.get("symbol"))
                symbol = str(position.get("symbol", ""))
                if not symbol.endswith("_USDT"):
                    raise ValueError("position symbol is invalid")
                if str(position.get("side")) not in {"LONG", "SHORT"}:
                    raise ValueError("position side is invalid")
                if _state_decimal(position.get("quantity"), "position.quantity") <= 0:
                    raise ValueError("position quantity must be positive")
                if _state_decimal(position.get("entry_price"), "position.entry_price") <= 0:
                    raise ValueError("position entry_price must be positive")
                if _state_decimal(position.get("entry_fee_usdt", 0), "position.entry_fee_usdt") < 0:
                    raise ValueError("position entry_fee_usdt must be non-negative")
            self._recovery_position = position
            if position is not None:
                self._position_exposed_during_outage = self._checkpoint_existed_at_start
                self._recovery_action = "MANUAL_RECOVERY_FLATTEN_REQUIRED"
                self._risk_halted = True
                self._risk_halt_reason = self._risk_halt_reason or "RECOVERY_REQUIRED"
            self._restore_risk_halt_for_current_window()
            if schema_version != STATE_SCHEMA_VERSION:
                raise IntegrityError(
                    f"checkpoint schema v{schema_version} is legacy/unattributed; "
                    "use new-paper-run after archiving"
                )
            self._load_v3_integrity(state)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            InvalidOperation,
            OverflowError,
            json.JSONDecodeError,
        ) as exc:
            self._set_state_invalid(str(exc))

    def _load_v3_integrity(self, state: dict[str, object]) -> None:
        run_id = str(state.get("run_id", ""))
        ledger_relative = Path(str(state.get("event_ledger_path", "")))
        ledger_path = (self._project_root / ledger_relative).resolve()
        project_root = self._project_root.resolve()
        if not ledger_path.is_relative_to(project_root):
            raise IntegrityError("event ledger path escapes the project root")
        metadata_path = project_root / "data" / "runs" / run_id / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError(f"run metadata is unavailable: {exc}") from exc
        if not isinstance(metadata, dict) or metadata.get("run_id") != run_id:
            raise IntegrityError("run metadata identity mismatch")
        if metadata.get("event_schema_version") != EVENT_SCHEMA_VERSION:
            raise IntegrityError("run metadata event schema mismatch")
        self._run_id = run_id
        self._events_path = ledger_path
        self._metadata = metadata
        self._checkpoint_sequence = int(str(state.get("checkpoint_sequence", -1)))
        if self._checkpoint_sequence < 0:
            raise IntegrityError("checkpoint_sequence must be non-negative")
        summary = scan_ledger(ledger_path, run_id)
        if summary.legacy_count:
            raise IntegrityError("current run ledger contains legacy/unattributed events")
        reconcile_checkpoint(state, summary)
        self._ledger = EventLedger(ledger_path, run_id, self._session_id)
        self._accounting_state = "VALID"
        self._accounting_error = None
        self._state_error = None
        self._rollover_review_required = bool(state.get("rollover_review_required", False))
        if self._recovery_position is not None:
            position_id = self._recovery_position.get("position_id")
            if position_id is not None:
                self._lifecycle[str(self._recovery_position["symbol"])] = {
                    "signal_id": str(self._recovery_position.get("signal_id") or "") or None,
                    "position_id": str(position_id),
                    "entry_order_id": None,
                    "exit_order_id": None,
                    "entry_client_order_id": None,
                    "exit_client_order_id": None,
                    "closing": None,
                    "position_open_timestamp": str(
                        self._recovery_position.get("position_open_timestamp") or ""
                    )
                    or None,
                }

    def _set_state_invalid(self, reason: str) -> None:
        self._state_error = reason[:200]
        self._accounting_state = "INVALID"
        self._accounting_error = self._state_error
        self._entry_enabled = False
        self._risk_halted = True
        self._risk_halt_reason = "STATE_INVALID"
        self.set_entry_enabled(False)

    def _load_risk_state(self, state: dict[str, object]) -> None:
        today = datetime.now(UTC).date().isoformat()
        risk_day = state.get("risk_day_utc")
        if isinstance(risk_day, str):
            datetime.strptime(risk_day, "%Y-%m-%d")
            self._risk_day_utc = risk_day
            self._day_start_equity = _state_decimal(
                state.get("day_start_equity_usdt"), "day_start_equity_usdt"
            )
            self._trades_at_day_start = int(str(state.get("trades_at_day_start", 0)))
        else:
            today_trades = [
                trade
                for trade in self._trade_history
                if str(trade.get("closed_at", "")).startswith(today)
            ]
            today_pnl = sum(
                (Decimal(str(trade["realized_pnl_usdt"])) for trade in today_trades),
                Decimal(0),
            )
            self._risk_day_utc = today
            self._day_start_equity = self._initial_balance - today_pnl
            self._trades_at_day_start = max(0, self._restored_trades - len(today_trades))
        if self._day_start_equity < 0 or self._trades_at_day_start < 0:
            raise ValueError("daily risk window is invalid")
        persisted_peak = _state_decimal(
            state.get("peak_equity_usdt", self._initial_balance),
            "peak_equity_usdt",
        )
        self._peak_equity = max(self._day_start_equity, self._initial_balance)
        if state.get("drawdown_window") == "UTC_DAY":
            self._peak_equity = max(self._peak_equity, persisted_peak)
        risk_halted = state.get("risk_halted", False)
        if not isinstance(risk_halted, bool):
            raise ValueError("risk_halted must be boolean")
        reason = state.get("risk_halt_reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 64):
            raise ValueError("risk_halt_reason is invalid")
        self._risk_halted = risk_halted
        self._risk_halt_reason = reason
        if (
            self._risk_halted
            and self._risk_halt_reason in {"DAILY_LOSS", "MAX_DRAWDOWN"}
            and self._risk_day_utc < today
        ):
            self._rollover_review_required = True

    def _restore_risk_halt_for_current_window(self) -> None:
        daily_pnl = self._initial_balance - self._day_start_equity
        drawdown = (
            (self._peak_equity - self._initial_balance) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        if daily_pnl <= -self._settings.strategy_daily_loss_usdt:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "DAILY_LOSS"
        if drawdown >= self._settings.strategy_max_drawdown_pct:
            self._risk_halted = True
            self._risk_halt_reason = self._risk_halt_reason or "MAX_DRAWDOWN"

    def _persist_state(self, *, rollover: bool = True) -> None:
        if self._engine is None or not self._strategies:
            return
        balance, equity, trades, fees, positions = self._accounting_totals()
        if rollover and datetime.now(UTC).date().isoformat() != self._risk_day_utc:
            self._roll_risk_day(equity, trades)
            return
        if len(positions) > 1:
            raise RuntimeError("cannot persist more than one paper position")
        position = None
        if positions:
            current = positions[0]
            symbol = self._symbol_for_position(current)
            strategy = self._strategies[symbol]
            position = {
                "symbol": symbol,
                "side": current.side.name,
                "quantity": str(current.quantity.as_decimal()),
                "entry_price": str(current.avg_px_open),
                "entry_fee_usdt": str(strategy.open_entry_fee_usdt),
                "position_id": self._lifecycle.get(symbol, {}).get("position_id"),
                "signal_id": self._lifecycle.get(symbol, {}).get("signal_id"),
                "position_open_timestamp": self._lifecycle.get(symbol, {}).get(
                    "position_open_timestamp"
                ),
                "market_scope": self._lifecycle.get(symbol, {}).get("market_scope"),
                "market_class": self._lifecycle.get(symbol, {}).get("market_class"),
                "strategy": self._lifecycle.get(symbol, {}).get("strategy"),
                "entry_reason": self._lifecycle.get(symbol, {}).get("entry_reason"),
                "regime": self._lifecycle.get(symbol, {}).get("regime"),
                "confidence": self._lifecycle.get(symbol, {}).get("confidence"),
                "risk_profile": self._lifecycle.get(symbol, {}).get("risk_profile"),
                "mode_at_entry": self._lifecycle.get(symbol, {}).get("mode_at_entry"),
                "confirmation_state": self._lifecycle.get(symbol, {}).get(
                    "confirmation_state"
                ),
                "session": self._lifecycle.get(symbol, {}).get("session"),
            }
        if self._ledger is None or self._run_id is None:
            self._set_state_invalid("current paper run has no v3 event ledger")
            raise RuntimeError(self._state_error)
        open_entry_fee = (
            _state_decimal(position["entry_fee_usdt"], "position.entry_fee_usdt")
            if position is not None
            else Decimal(0)
        )
        self._cumulative_realized_pnl = balance - self._starting_equity + open_entry_fee
        self._checkpoint_sequence += 1
        heartbeat = utc_now()
        self._last_successful_heartbeat_utc = heartbeat
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "checkpoint_sequence": self._checkpoint_sequence,
            "last_event_sequence": self._ledger.last_sequence,
            "last_committed_event_sequence": self._ledger.last_sequence,
            "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
            "run_id": self._run_id,
            "session_id": self._session_id,
            "event_ledger_path": str(self._events_path.relative_to(self._project_root)),
            "mode": "PAPER",
            "symbol": "MULTI",
            "starting_equity_usdt": str(self._starting_equity),
            "balance_usdt": str(balance),
            "cumulative_realized_pnl_usdt": str(self._cumulative_realized_pnl),
            "trades": trades,
            "fees_usdt": str(fees),
            "position": position,
            "risk_day_utc": self._risk_day_utc,
            "day_start_equity_usdt": str(self._day_start_equity),
            "trades_at_day_start": self._trades_at_day_start,
            "strategy_active_seconds": str(self._strategy_active_seconds),
            "peak_equity_usdt": str(self._peak_equity),
            "drawdown_window": "UTC_DAY",
            "risk_halted": self._risk_halted,
            "risk_halt_reason": self._risk_halt_reason,
            "rollover_review_required": self._rollover_review_required,
            **self._recovery_checkpoint_fields(),
            "updated_at_utc": heartbeat,
            "updated_ns": time_ns(),
        }
        self._commit_checkpoint(state)

    def _accounting_totals(
        self,
    ) -> tuple[Decimal, Decimal, int, Decimal, list[Any]]:
        if self._engine is None:
            raise RuntimeError("paper engine is unavailable")
        account = self._engine.cache.account_for_venue(Venue(self._settings.venue))
        balance_money = account.balance_total(Currency.from_str("USDT")) if account else None
        if balance_money is None:
            raise RuntimeError("paper account balance is unavailable")
        balance = balance_money.as_decimal()
        positions = list(self._engine.cache.positions_open())
        unrealized = Decimal(0)
        for current in positions:
            symbol = self._symbol_for_position(current)
            mark = self._last_marks.get(symbol)
            if mark is not None:
                unrealized += current.unrealized_pnl(mark).as_decimal()
        trades = self._restored_trades + sum(
            strategy.trades for strategy in self._strategies.values()
        )
        fees = self._restored_fees + sum(
            (strategy.fees_usdt for strategy in self._strategies.values()), Decimal(0)
        )
        return balance, balance + unrealized, trades, fees, positions

    def _flatten_recovery(self) -> None:
        if self._recovery_position is None:
            raise RuntimeError("no recovery position exists")
        symbol = str(self._recovery_position.get("symbol", ""))
        market = self._last_markets.get(symbol)
        received = self._last_market_monotonic.get(symbol)
        if (
            market is None
            or received is None
            or monotonic() - received > self._settings.market_stale_after_seconds
        ):
            raise RuntimeError("recovery position has no fresh market")
        side = str(self._recovery_position.get("side"))
        quantity = Decimal(str(self._recovery_position["quantity"]))
        entry = Decimal(str(self._recovery_position["entry_price"]))
        entry_fee = Decimal(str(self._recovery_position.get("entry_fee_usdt", 0)))
        raw_bid = Decimal(str(market["bid"]))
        raw_ask = Decimal(str(market["ask"]))
        increment = Decimal(str(market.get("price_increment", "0.00000001")))
        modeled_bid, modeled_ask = construct_pessimistic_prices(
            raw_bid,
            raw_ask,
            increment,
            self._settings.strategy_slippage_bps,
        )
        if side == "LONG":
            exit_price = modeled_bid
            gross = (exit_price - entry) * quantity
        elif side == "SHORT":
            exit_price = modeled_ask
            gross = (entry - exit_price) * quantity
        else:
            raise RuntimeError("recovery position side is invalid")
        exit_fee = exit_price * quantity * TAKER_FEE
        self._initial_balance += gross - exit_fee
        self._restored_fees += exit_fee
        self._restored_trades += 1
        trade_pnl = gross - entry_fee - exit_fee
        entry_notional = entry * quantity
        closed_at = utc_now()
        opened_at = self._recovery_position.get("position_open_timestamp")
        holding_time_ms = duration_ms(str(opened_at), closed_at) if opened_at is not None else None
        raw_mid = (raw_bid + raw_ask) / 2
        spread_cost = (
            max(Decimal(0), raw_mid - raw_bid) * quantity
            if side == "LONG"
            else max(Decimal(0), raw_ask - raw_mid) * quantity
        )
        modeled_slippage = (
            max(Decimal(0), raw_bid - exit_price) * quantity
            if side == "LONG"
            else max(Decimal(0), exit_price - raw_ask) * quantity
        )
        self._write_events(
            [
                {
                    "event": "recovery_flatten",
                    "timestamp_utc": closed_at,
                    "side": side,
                    "quantity": str(quantity),
                    "price": str(exit_price),
                    "open_price": str(entry),
                    "close_price": str(exit_price),
                    "realized_pnl_usdt": str(trade_pnl),
                    "pnl_pct": str(trade_pnl / entry_notional * 100),
                    "fee_usdt": str(entry_fee + exit_fee),
                    "ledger_fee_usdt": str(exit_fee),
                    "total_fee_usdt": str(entry_fee + exit_fee),
                    "reason": "RECOVERY_FLATTEN",
                    "position_id": self._recovery_position.get("position_id"),
                    "signal_id": self._recovery_position.get("signal_id"),
                    "recovery_identity_status": (
                        "KNOWN"
                        if self._recovery_position.get("position_id") is not None
                        else "UNKNOWN"
                    ),
                    "classification": "OUTAGE_HELD",
                    "holding_time_ms": holding_time_ms,
                    "position_open_timestamp": opened_at,
                    "position_close_timestamp": closed_at,
                    "entry_notional_usdt": str(entry_notional),
                    "exit_notional_usdt": str(exit_price * quantity),
                    "gross_price_pnl_usdt": str(gross),
                    "entry_fee_usdt": str(entry_fee),
                    "exit_fee_usdt": str(exit_fee),
                    "maker_taker": "TAKER",
                    "spread_cost_usdt": str(spread_cost),
                    "modeled_slippage_usdt": str(modeled_slippage),
                    "slippage_embedded_in_fill_price": True,
                    "funding_usdt": "0",
                    "funding_status": "NOT_MODELED",
                    "other_execution_adjustments_usdt": "0",
                    "outage_started_at_utc": self._outage_started_at_utc,
                    "restart_detected_at_utc": self._restart_detected_at_utc,
                    "outage_duration_ms": self._outage_duration_ms,
                }
            ],
            symbol,
        )
        self._recovery_position = None
        self._recovery_action = "RECOVERY_FLATTEN_COMPLETED"
        self._recovery_completed_at_utc = closed_at
        if self._risk_halt_reason == "RECOVERY_REQUIRED":
            self._risk_halted = False
            self._risk_halt_reason = None
        self._peak_equity = max(self._peak_equity, self._initial_balance)
        self._restore_risk_halt_for_current_window()
        if self._ledger is None or self._run_id is None:
            self._set_state_invalid("recovery has no durable v3 ledger")
            raise RuntimeError(self._state_error)
        self._cumulative_realized_pnl = self._initial_balance - self._starting_equity
        self._checkpoint_sequence += 1
        self._last_successful_heartbeat_utc = closed_at
        self._commit_checkpoint(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "checkpoint_sequence": self._checkpoint_sequence,
                "last_event_sequence": self._ledger.last_sequence,
                "last_committed_event_sequence": self._ledger.last_sequence,
                "transition_protocol_version": TRANSITION_PROTOCOL_VERSION,
                "run_id": self._run_id,
                "session_id": self._session_id,
                "event_ledger_path": str(self._events_path.relative_to(self._project_root)),
                "mode": "PAPER",
                "symbol": "MULTI",
                "starting_equity_usdt": str(self._starting_equity),
                "balance_usdt": str(self._initial_balance),
                "cumulative_realized_pnl_usdt": str(self._cumulative_realized_pnl),
                "trades": self._restored_trades,
                "fees_usdt": str(self._restored_fees),
                "position": None,
                "risk_day_utc": self._risk_day_utc,
                "day_start_equity_usdt": str(self._day_start_equity),
                "trades_at_day_start": self._trades_at_day_start,
                "strategy_active_seconds": str(self._strategy_active_seconds),
                "peak_equity_usdt": str(self._peak_equity),
                "drawdown_window": "UTC_DAY",
                "risk_halted": self._risk_halted,
                "risk_halt_reason": self._risk_halt_reason,
                "rollover_review_required": self._rollover_review_required,
                **self._recovery_checkpoint_fields(),
                "updated_at_utc": closed_at,
                "updated_ns": time_ns(),
            }
        )

    def _commit_checkpoint(self, state: dict[str, object]) -> None:
        if self._ledger is None:
            raise RuntimeError("paper event ledger is unavailable")
        try:
            if self._pending_event_specs:
                committed = commit_transition(
                    self._ledger,
                    self._state_path,
                    state,
                    self._pending_event_specs,
                )
            else:
                reconcile_checkpoint(state, self._ledger.summary)
                self._write_state(state)
                committed = []
        except (IntegrityError, OSError, ValueError) as exc:
            self._set_state_invalid(str(exc))
            raise
        self._pending_event_specs.clear()
        self._cumulative_realized_pnl = self._ledger.summary.realized_pnl
        for event in committed:
            record = self._trade_record(event)
            if record is not None:
                self._trade_history.append(record)
        if any(
            event.get("event_type") in {"position_closed", "recovery_flatten"}
            for event in committed
        ):
            self._analytics_trades = self._load_analytics_trades()
        self._trade_history = list(reversed(self._recent_trade_history()))

    def _write_state(self, state: dict[str, object]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(state, file, allow_nan=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self._state_path)

    def _portfolio(
        self,
        equity: Decimal,
        fees: Decimal,
        trades: int,
        positions: int,
    ) -> PortfolioSnapshot:
        drawdown = (
            (self._peak_equity - equity) / self._peak_equity * 100
            if self._peak_equity
            else Decimal(0)
        )
        known_slippage = [
            Decimal(str(trade["modeled_slippage_usdt"]))
            for trade in self._analytics_trades
            if trade.get("modeled_slippage_usdt") is not None
        ]
        return {
            "equity_usdt": float(equity),
            "daily_pnl_usdt": float(equity - self._day_start_equity),
            "drawdown_pct": float(drawdown),
            "trades_today": max(0, trades - self._trades_at_day_start),
            "open_positions": positions,
            "fees_usdt": float(fees),
            "slippage_usdt": float(sum(known_slippage, Decimal(0))) if known_slippage else None,
        }

    def _write_events(self, events: list[dict[str, object]], symbol: str) -> None:
        if not events:
            return
        if self._ledger is None:
            raise RuntimeError("refusing to write unversioned paper events")
        for raw_event in events:
            self._pending_event_specs.append(self._prepare_strategy_event(raw_event, symbol))

    def _prepare_strategy_event(
        self,
        raw_event: dict[str, object],
        symbol: str,
    ) -> EventSpec:
        if self._ledger is None:
            raise RuntimeError("paper event ledger is unavailable")
        event_type = str(raw_event.get("event", ""))
        observed = str(raw_event.get("timestamp_utc") or utc_now())
        observed = canonical_utc(observed)
        context = self._lifecycle.setdefault(
            symbol,
            {
                "signal_id": None,
                "position_id": None,
                "entry_order_id": None,
                "exit_order_id": None,
                "entry_client_order_id": None,
                "exit_client_order_id": None,
                "closing": None,
                "position_open_timestamp": None,
            },
        )
        if event_type == "signal":
            default_scope = (
                MarketScope.XAU_ONLY.value
                if symbol == self._settings.xau.execution_symbol
                else MarketScope.WIDE_CRYPTO.value
            )
            context.update(
                {
                    "signal_id": new_identity(),
                    "position_id": new_identity(),
                    "entry_order_id": None,
                    "exit_order_id": None,
                    "entry_client_order_id": None,
                    "exit_client_order_id": None,
                    "closing": None,
                    "position_open_timestamp": None,
                    "market_scope": raw_event.get("market_scope", default_scope),
                    "market_class": raw_event.get(
                        "market_class", "xau" if default_scope == "XAU_ONLY" else "crypto"
                    ),
                    "strategy": raw_event.get(
                        "strategy",
                        "xau_trend_v1" if default_scope == "XAU_ONLY" else STRATEGY_VERSION,
                    ),
                    "entry_reason": raw_event.get("entry_reason", "QUALIFIED_MOMENTUM"),
                    "regime": raw_event.get("regime", "MOMENTUM"),
                    "confidence": raw_event.get("confidence"),
                    "risk_profile": raw_event.get(
                        "risk_profile", "XAU" if default_scope == "XAU_ONLY" else "CRYPTO"
                    ),
                    "mode_at_entry": raw_event.get("mode_at_entry", self._report.mode),
                    "confirmation_state": raw_event.get(
                        "confirmation_state", "NOT_APPLICABLE"
                    ),
                    "session": raw_event.get("session", "UNCLASSIFIED"),
                }
            )
        elif event_type == "order_submitted":
            context["entry_order_id"] = context["entry_order_id"] or new_identity()
            context["entry_client_order_id"] = str(raw_event.get("client_order_id"))
        elif event_type == "exit_signal":
            context["closing"] = "true"
            context["exit_order_id"] = new_identity()
        elif event_type == "position_opened":
            context["position_open_timestamp"] = str(
                raw_event.get("exchange_timestamp") or observed
            )

        closing = context["closing"] == "true"
        order_id = context["exit_order_id"] if closing else context["entry_order_id"]
        client_order_id = (
            context["exit_client_order_id"] if closing else context["entry_client_order_id"]
        )
        if event_type == "fill":
            raw_client_order_id = str(raw_event.get("client_order_id") or "")
            if closing:
                context["exit_client_order_id"] = raw_client_order_id
                client_order_id = raw_client_order_id
            else:
                context["entry_client_order_id"] = raw_client_order_id
                client_order_id = raw_client_order_id

        values: dict[str, object] = {
            "timestamp_utc": observed,
            "timestamp_exchange": raw_event.get("exchange_timestamp"),
            "timestamp_local_or_receive": observed,
            "symbol": symbol,
            "strategy_id": STRATEGY_VERSION,
            "signal_id": context["signal_id"],
            "position_id": context["position_id"],
            "order_id": order_id,
            "client_order_id": client_order_id,
            "fill_id": new_identity() if event_type == "fill" else None,
            "side": raw_event.get("side") or raw_event.get("direction"),
            "order_type": raw_event.get("order_type"),
            "quantity": raw_event.get("quantity"),
            "price": raw_event.get("price"),
            "fee_amount": raw_event.get("ledger_fee_usdt", raw_event.get("fee_usdt")),
            "fee_currency": "USDT" if raw_event.get("fee_usdt") is not None else None,
            "reason": raw_event.get("reason"),
            "exchange_timestamp": raw_event.get("exchange_timestamp"),
            "local_receive_timestamp": observed,
            "decision_timestamp": observed if event_type in {"signal", "exit_signal"} else None,
            "order_submit_timestamp": observed if event_type == "order_submitted" else None,
            "fill_timestamp": observed if event_type == "fill" else None,
            "latency_ms": None,
            "market_scope": context.get("market_scope"),
            "market_class": context.get("market_class"),
            "strategy": context.get("strategy"),
            "entry_reason": context.get("entry_reason"),
            "regime": context.get("regime"),
            "confidence": context.get("confidence"),
            "risk_profile": context.get("risk_profile"),
            "mode_at_entry": context.get("mode_at_entry"),
            "confirmation_state": context.get("confirmation_state"),
            "session": context.get("session"),
        }
        if event_type == "order_rejected":
            values["order_id"] = order_id or new_identity()
        elif event_type == "recovery_flatten":
            values["signal_id"] = raw_event.get("signal_id")
            values["position_id"] = raw_event.get("position_id")
            values["order_id"] = new_identity()
            values["client_order_id"] = "RECOVERY_FLATTEN"
            values["fill_id"] = new_identity()
            values["fill_timestamp"] = observed
        for key, value in raw_event.items():
            if key not in {"event", "timestamp_utc", "fee_usdt", "exchange_timestamp"}:
                values.setdefault(key, value)
        if event_type == "position_closed":
            opened_at = context.get("position_open_timestamp")
            closed_at = str(raw_event.get("exchange_timestamp") or observed)
            values["position_open_timestamp"] = opened_at
            values["position_close_timestamp"] = closed_at
            if opened_at is not None:
                opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                closed = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                values["holding_time_ms"] = max(0, round((closed - opened).total_seconds() * 1000))
            values["classification"] = (
                "MANUAL" if "MANUAL" in str(raw_event.get("reason", "")).upper() else "NORMAL"
            )
        if event_type == "position_closed":
            self._lifecycle.pop(symbol, None)
        return event_type, values

    def _open_trade(self, position: object | None) -> dict[str, object] | None:
        if position is not None:
            symbol = self._symbol_for_position(position)
            market = self._last_markets.get(symbol)
            entry = Decimal(str(position.avg_px_open))  # type: ignore[attr-defined]
            current = (
                Decimal(str(market["last"]))
                if market is not None and market.get("last") is not None
                else None
            )
            side = position.side.name  # type: ignore[attr-defined]
            direction = Decimal(1) if side == "LONG" else Decimal(-1)
            pnl_pct = (
                float((current / entry - 1) * direction * 100)
                if current is not None and entry > 0
                else None
            )
            stop_bps = (
                self._settings.xau.stop_loss_bps
                if symbol == self._settings.xau.execution_symbol
                else self._settings.strategy_stop_loss_bps
            )
            target_bps = (
                self._settings.xau.take_profit_bps
                if symbol == self._settings.xau.execution_symbol
                else self._settings.strategy_take_profit_bps
            )
            lifecycle = self._lifecycle.get(symbol, {})
            return {
                "symbol": symbol,
                "side": side,
                "quantity": float(position.quantity.as_decimal()),  # type: ignore[attr-defined]
                "entry_price": position.avg_px_open,  # type: ignore[attr-defined]
                "current_price": market.get("last") if market is not None else None,
                "pnl_pct": pnl_pct,
                "stop_price": float(
                    entry * (Decimal(1) - direction * stop_bps / Decimal(10_000))
                ),
                "target_price": float(
                    entry * (Decimal(1) + direction * target_bps / Decimal(10_000))
                ),
                "market_scope": lifecycle.get("market_scope"),
                "regime": lifecycle.get("regime"),
                "confidence": lifecycle.get("confidence"),
                "leverage": float(self._settings.leverage),
            }
        if self._recovery_position is not None:
            symbol = str(self._recovery_position.get("symbol", ""))
            market = self._last_markets.get(symbol)
            return {
                "symbol": symbol,
                "side": self._recovery_position.get("side"),
                "quantity": self._recovery_position.get("quantity"),
                "entry_price": self._recovery_position.get("entry_price"),
                "current_price": market.get("last") if market is not None else None,
            }
        return None

    def _load_trade_history(self) -> list[dict[str, object]]:
        if not self._events_path.is_file():
            return []
        records = []
        entry_fills: dict[str, list[dict[str, object]]] = {}
        exit_fills: dict[str, list[dict[str, object]]] = {}
        closing: set[str] = set()
        try:
            with self._events_path.open(encoding="utf-8") as file:
                for line in file:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("schema_version") == EVENT_SCHEMA_VERSION:
                        record = self._trade_record(event)
                        if record is not None:
                            records.append(record)
                        continue
                    symbol = event.get("symbol")
                    event_type = event.get("event")
                    if isinstance(symbol, str):
                        if event_type == "signal":
                            entry_fills[symbol] = []
                            exit_fills[symbol] = []
                            closing.discard(symbol)
                        elif event_type == "fill" and symbol in entry_fills:
                            target = exit_fills if symbol in closing else entry_fills
                            target[symbol].append(event)
                        elif event_type == "exit_signal" and symbol in entry_fills:
                            closing.add(symbol)
                        elif event_type == "position_closed" and symbol in entry_fills:
                            event = self._aggregate_trade_fills(
                                event,
                                entry_fills.pop(symbol),
                                exit_fills.pop(symbol),
                            )
                            closing.discard(symbol)
                    record = self._trade_record(event)
                    if record is not None:
                        records.append(record)
        except OSError:
            return []
        return records

    @staticmethod
    def _aggregate_trade_fills(
        event: dict[str, object],
        entry_fills: list[dict[str, object]],
        exit_fills: list[dict[str, object]],
    ) -> dict[str, object]:
        try:
            entry_quantity, entry_notional, entry_fee = _fill_totals(entry_fills)
            exit_quantity, exit_notional, exit_fee = _fill_totals(exit_fills)
            realized = Decimal(str(event["realized_pnl_usdt"]))
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return event
        if not all(
            value.is_finite()
            for value in (
                entry_quantity,
                entry_notional,
                entry_fee,
                exit_quantity,
                exit_notional,
                exit_fee,
                realized,
            )
        ):
            return event
        tolerance = max(Decimal("0.000001"), entry_quantity * Decimal("0.000001"))
        if (
            entry_quantity <= 0
            or exit_quantity <= 0
            or abs(entry_quantity - exit_quantity) > tolerance
        ):
            return event
        return {
            **event,
            "quantity": float(entry_quantity),
            "open_price": float(entry_notional / entry_quantity),
            "close_price": float(exit_notional / exit_quantity),
            "fee_usdt": float(entry_fee + exit_fee),
            "pnl_pct": float(realized / entry_notional * 100),
        }

    def _recent_trade_history(self) -> list[dict[str, object]]:
        cutoff = datetime.now(UTC) - timedelta(hours=TRADE_HISTORY_HOURS)
        recent = [
            record
            for record in self._trade_history
            if datetime.fromisoformat(str(record["closed_at"]).replace("Z", "+00:00")) >= cutoff
        ]
        return list(reversed(recent))

    @staticmethod
    def _trade_record(event: object) -> dict[str, object] | None:
        if not isinstance(event, dict) or event.get("event_type", event.get("event")) not in {
            "position_closed",
            "recovery_flatten",
        }:
            return None
        try:
            closed_at = str(event["timestamp_utc"])
            parsed_time = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            values = {
                key: float(event[key])
                for key in (
                    "quantity",
                    "open_price",
                    "close_price",
                    "realized_pnl_usdt",
                    "pnl_pct",
                )
            }
            if event.get("total_trade_pnl_usdt") is not None:
                values["realized_pnl_usdt"] = float(event["total_trade_pnl_usdt"])
            fee_value = (
                event["total_fee_usdt"]
                if "total_fee_usdt" in event
                else event["fee_amount"]
                if "fee_amount" in event
                else event["fee_usdt"]
            )
            values["fee_usdt"] = float(fee_value)
            symbol = str(event["symbol"])
            side = str(event["side"])
            reason = str(event["reason"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            parsed_time.tzinfo is None
            or not all(math.isfinite(value) for value in values.values())
            or values["quantity"] <= 0
            or values["open_price"] <= 0
            or values["close_price"] <= 0
            or values["fee_usdt"] < 0
            or side not in {"LONG", "SHORT"}
            or not symbol
            or len(symbol) > 32
            or not reason
            or len(reason) > 64
        ):
            return None
        return {
            "closed_at": parsed_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "symbol": symbol,
            "side": side,
            **values,
            "reason": reason,
            "position_id": event.get("position_id"),
            "holding_time_ms": event.get("holding_time_ms"),
            "classification": classify_close(event),
            "market_scope": event.get(
                "market_scope",
                "XAU_ONLY" if symbol == "XAU_USDT" else "WIDE_CRYPTO",
            ),
            "market_class": event.get(
                "market_class", "xau" if symbol == "XAU_USDT" else "crypto"
            ),
            "strategy": event.get("strategy", event.get("strategy_id")),
            "entry_reason": event.get("entry_reason"),
            "regime": event.get("regime"),
            "confidence": event.get("confidence"),
            "risk_profile": event.get("risk_profile"),
            "mode_at_entry": event.get("mode_at_entry"),
            "confirmation_state": event.get("confirmation_state"),
            "session": event.get("session", "UNCLASSIFIED"),
            "entry_notional_usdt": float(
                event.get("entry_notional_usdt", values["quantity"] * values["open_price"])
            ),
            "exit_notional_usdt": float(
                event.get("exit_notional_usdt", values["quantity"] * values["close_price"])
            ),
            "gross_price_pnl_usdt": float(
                event.get(
                    "gross_price_pnl_usdt",
                    (values["close_price"] - values["open_price"])
                    * values["quantity"]
                    * (1 if side == "LONG" else -1),
                )
            ),
            "entry_fee_usdt": (
                float(event["entry_fee_usdt"]) if event.get("entry_fee_usdt") is not None else None
            ),
            "exit_fee_usdt": (
                float(event["exit_fee_usdt"]) if event.get("exit_fee_usdt") is not None else None
            ),
            "modeled_slippage_usdt": (
                float(event["modeled_slippage_usdt"])
                if event.get("modeled_slippage_usdt") is not None
                else None
            ),
            "spread_cost_usdt": (
                float(event["spread_cost_usdt"])
                if event.get("spread_cost_usdt") is not None
                else None
            ),
        }


def _decimal_places(value: object) -> int:
    exponent = Decimal(str(value)).normalize().as_tuple().exponent
    if not isinstance(exponent, int):
        return 8
    return max(0, -exponent)


def construct_pessimistic_prices(
    raw_bid: Decimal,
    raw_ask: Decimal,
    price_increment: Decimal,
    slippage_bps: Decimal,
) -> tuple[Decimal, Decimal]:
    values = (raw_bid, raw_ask, price_increment, slippage_bps)
    if not all(value.is_finite() for value in values):
        raise QuoteValidationError("quote inputs must be finite")
    if raw_bid <= 0 or raw_ask <= 0 or raw_bid >= raw_ask:
        raise QuoteValidationError("raw quote must satisfy 0 < bid < ask")
    if price_increment <= 0 or slippage_bps < 0:
        raise QuoteValidationError("tick size and slippage are invalid")
    slippage = slippage_bps / Decimal(10_000)
    modeled_bid = raw_bid * (Decimal(1) - slippage)
    modeled_ask = raw_ask * (Decimal(1) + slippage)
    bid = (modeled_bid / price_increment).to_integral_value(rounding=ROUND_FLOOR) * price_increment
    ask = (modeled_ask / price_increment).to_integral_value(
        rounding=ROUND_CEILING
    ) * price_increment
    if bid <= 0:
        raise QuoteValidationError("modeled bid rounded to zero")
    if ask <= bid:
        ask = bid + price_increment
    if ask <= bid:
        raise QuoteValidationError("modeled quote remains crossed after tick repair")
    return bid, ask


def _state_decimal(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _ns_to_utc(timestamp_ns: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ns / 1_000_000_000, UTC).isoformat().replace("+00:00", "Z")
    )


def _fill_totals(fills: list[dict[str, object]]) -> tuple[Decimal, Decimal, Decimal]:
    quantity = Decimal(0)
    notional = Decimal(0)
    fees = Decimal(0)
    for fill in fills:
        fill_quantity = Decimal(str(fill["quantity"]))
        fill_price = Decimal(str(fill["price"]))
        fill_fee = Decimal(str(fill["fee_usdt"]))
        if fill_quantity <= 0 or fill_price <= 0 or fill_fee < 0:
            raise ValueError("invalid fill values")
        quantity += fill_quantity
        notional += fill_quantity * fill_price
        fees += fill_fee
    return quantity, notional, fees
